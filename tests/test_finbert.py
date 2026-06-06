"""Tests for features/finbert.py — FinBERT scorer with mocked pipeline.

transformers and the real FinBERT model are NOT required for these unit tests.
All HuggingFace pipeline calls are patched with deterministic fixtures.

Integration tests (real model, --integration flag) live in test_integration.py.
"""
from __future__ import annotations

from unittest.mock import patch

import pandas as pd
import pytest


def _mock_pipe(label: str = "positive", score: float = 0.8):
    """Returns a callable that mimics the HF pipeline with fixed scores."""
    pos = score if label == "positive" else 0.1
    neg = score if label == "negative" else 0.1
    neu = max(0.0, 1.0 - pos - neg)

    def _call(texts, truncation=True, max_length=512):
        return [
            [{"label": "positive", "score": pos},
             {"label": "negative", "score": neg},
             {"label": "neutral",  "score": neu}]
            for _ in texts
        ]
    return _call


@pytest.fixture(autouse=True)
def _reset_pipeline():
    import quant.features.finbert as fb
    original = fb._pipeline
    fb._pipeline = None
    yield
    fb._pipeline = original


class TestScore:
    def test_empty_returns_empty(self):
        from quant.features.finbert import score
        assert score([]) == []

    def test_single_text_returns_one_float(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe("positive", 0.8)):
            from quant.features.finbert import score
            result = score(["revenue beat expectations"])
        assert len(result) == 1
        assert isinstance(result[0], float)

    def test_score_in_range(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe("positive", 0.7)):
            from quant.features.finbert import score
            result = score(["any text"])
        assert -1.0 <= result[0] <= 1.0

    def test_positive_mock_gives_positive_score(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe("positive", 0.9)):
            from quant.features.finbert import score
            result = score(["strong beat"])
        assert result[0] > 0

    def test_negative_mock_gives_negative_score(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe("negative", 0.9)):
            from quant.features.finbert import score
            result = score(["missed guidance"])
        assert result[0] < 0

    def test_batch_size_respected(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()):
            from quant.features.finbert import score
            result = score([f"text {i}" for i in range(10)], batch_size=3)
        assert len(result) == 10

    def test_long_text_does_not_raise(self):
        long_text = "word " * 1000  # well over 512 tokens
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()):
            from quant.features.finbert import score
            result = score([long_text])
        assert len(result) == 1


class TestScoreDocuments:
    def _docs(self, n: int = 3) -> pd.DataFrame:
        return pd.DataFrame({
            "document_id": [f"doc_{i}" for i in range(n)],
            "symbol": "AAPL",
            "published_at": pd.date_range("2023-01-02", periods=n, tz="UTC"),
            "text": [f"text {i}" for i in range(n)],
        })

    def test_empty_returns_empty_df(self):
        from quant.features.finbert import score_documents
        assert score_documents(pd.DataFrame()).empty

    def test_required_columns_present(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()):
            from quant.features.finbert import score_documents
            result = score_documents(self._docs())
        for col in ("document_id", "symbol", "published_at", "scored_at",
                    "model_name", "model_version",
                    "sentiment_positive", "sentiment_negative",
                    "sentiment_neutral", "sentiment_score"):
            assert col in result.columns

    def test_row_count_matches_input(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()):
            from quant.features.finbert import score_documents
            assert len(score_documents(self._docs(5))) == 5

    def test_scored_at_is_tz_aware(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()):
            from quant.features.finbert import score_documents
            result = score_documents(self._docs())
        assert result["scored_at"].dt.tz is not None

    def test_scores_in_valid_range(self):
        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()):
            from quant.features.finbert import score_documents
            result = score_documents(self._docs(8))
        assert result["sentiment_score"].between(-1.0, 1.0).all()
        assert result["sentiment_positive"].between(0.0, 1.0).all()
        assert result["sentiment_negative"].between(0.0, 1.0).all()


class TestRunScoring:
    def _make_docs(self, ids: list[str] | None = None) -> pd.DataFrame:
        ids = ids or ["doc_0", "doc_1", "doc_2"]
        return pd.DataFrame({
            "document_id": ids,
            "symbol": "AAPL",
            "published_at": pd.date_range("2023-01-02", periods=len(ids), tz="UTC"),
            "text": [f"text for {i}" for i in ids],
        })

    def test_new_docs_written(self):
        from quant.features.finbert import run_scoring
        with (
            patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()),
            patch("quant.features.finbert.lake.read_processed", return_value=pd.DataFrame()),
            patch("quant.features.finbert.lake.write_processed") as mock_write,
        ):
            n = run_scoring(docs_df=self._make_docs())
        assert n == 3
        assert len(mock_write.call_args[0][0]) == 3

    def test_re_scoring_overwrites_not_duplicates(self):
        """Calling run_scoring twice with the same docs produces no duplicate rows."""
        from quant.features.finbert import run_scoring, score_documents
        docs = self._make_docs()

        with patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe("positive", 0.8)):
            first_scored = score_documents(docs)

        with (
            patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe("negative", 0.9)),
            patch("quant.features.finbert.lake.read_processed", return_value=first_scored),
            patch("quant.features.finbert.lake.write_processed") as mock_write,
        ):
            run_scoring(docs_df=docs)

        written = mock_write.call_args[0][0]
        assert len(written) == 3
        assert (written["sentiment_score"] < 0).all()

    def test_already_scored_docs_skipped(self):
        """run_scoring() without docs_df only scores documents not in sentiment_scored/."""
        from quant.features.finbert import run_scoring
        all_docs = self._make_docs(["doc_0", "doc_1", "doc_2"])
        existing_scored = pd.DataFrame({
            "document_id": ["doc_0", "doc_1"],
            "symbol": "AAPL",
            "published_at": pd.date_range("2023-01-02", periods=2, tz="UTC"),
            "scored_at": [pd.Timestamp.now(tz="UTC")] * 2,
            "model_name": "ProsusAI/finbert",
            "model_version": "1.0.0",
            "sentiment_positive": [0.7, 0.3],
            "sentiment_negative": [0.1, 0.6],
            "sentiment_neutral": [0.2, 0.1],
            "sentiment_score": [0.6, -0.3],
        })

        def fake_read(dataset):
            return all_docs if dataset == "text_documents" else existing_scored

        with (
            patch("quant.features.finbert._get_pipeline", return_value=_mock_pipe()),
            patch("quant.features.finbert.lake.read_processed", side_effect=fake_read),
            patch("quant.features.finbert.lake.write_processed"),
        ):
            n = run_scoring()

        assert n == 1  # only doc_2 was newly scored


class TestGetPipeline:
    def test_missing_transformers_raises_importerror(self):
        import builtins
        original = builtins.__import__

        def _block(name, *args, **kwargs):
            if name == "transformers":
                raise ImportError("No module named 'transformers'")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_block):
            from quant.features.finbert import _get_pipeline
            with pytest.raises(ImportError, match="transformers is required"):
                _get_pipeline()
