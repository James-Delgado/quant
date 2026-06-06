"""FinBERT sentiment scorer.

Loads ProsusAI/finbert once and scores batches of text documents.
Writes a sentiment_scored/ dataset with one row per document.

Design decisions (per Phase 3 eng review):
- 512-token truncation (Option A): prototype speed over completeness.
  Future work: structured section extraction (Item 7 MD&A, Item 1A Risk
  Factors) via regex or LLM/SLM, which would improve signal for long 10-Ks.
- Device: auto-selects mps > cuda > cpu at load time (configurable via
  settings.finbert_device).
- Model loaded once at module level — not reloaded per symbol or per call.
  Call score_documents() for batch inference; do not call per-document.
- run_scoring() is idempotent: it merges with existing scored data and
  deduplicates by document_id, keeping the latest scored_at.

To pre-cache the model before going offline:
    python -m quant.features.finbert --download
"""
from __future__ import annotations

import logging

import pandas as pd

from quant.config import settings
from quant.ingest.schemas import SENTIMENT_SCORED_SCHEMA
from quant.storage import lake

logger = logging.getLogger(__name__)

MODEL_NAME = "ProsusAI/finbert"
MODEL_VERSION = "1.0.0"
DATASET = "sentiment_scored"

# Lazily initialised on first call — avoids import-time 500MB model load.
_pipeline = None


def _get_pipeline():
    """Load FinBERT pipeline once; auto-select device."""
    global _pipeline
    if _pipeline is not None:
        return _pipeline

    try:
        from transformers import pipeline as hf_pipeline
    except ImportError as exc:
        raise ImportError(
            "transformers is required for FinBERT inference. "
            "Install with: pip install transformers torch"
        ) from exc

    device_pref = settings.finbert_device.lower()
    if device_pref == "auto":
        try:
            import torch
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        except ImportError:
            device = "cpu"
    else:
        device = device_pref

    logger.info("Loading FinBERT on device=%s (model=%s)", device, MODEL_NAME)
    try:
        _pipeline = hf_pipeline(
            "text-classification",
            model=MODEL_NAME,
            device=device,
            top_k=None,  # return all 3 labels
        )
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load FinBERT ({exc}). "
            "Pre-cache with: python -m quant.features.finbert --download"
        ) from exc

    return _pipeline


def score(texts: list[str], batch_size: int = 32) -> list[float]:
    """Score a list of texts. Returns net sentiment (positive − negative) in [−1, 1].

    Each text is truncated to 512 tokens (BERT hard limit). For short 8-K
    disclosures this captures the full text. For long 10-Ks only the first
    ~350 words are scored — see module docstring for planned future work.
    """
    if not texts:
        return []

    pipe = _get_pipeline()
    scores: list[float] = []

    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        results = pipe(batch, truncation=True, max_length=512)
        for label_scores in results:
            pos = next((s["score"] for s in label_scores if s["label"] == "positive"), 0.0)
            neg = next((s["score"] for s in label_scores if s["label"] == "negative"), 0.0)
            scores.append(float(pos - neg))

    return scores


def score_documents(docs_df: pd.DataFrame, batch_size: int = 32) -> pd.DataFrame:
    """Score a DataFrame of text documents. Returns SENTIMENT_SCORED rows.

    Re-running on the same document_ids is idempotent — merge+dedup in
    run_scoring() overwrites old rows rather than duplicating them.
    """
    if docs_df.empty:
        return pd.DataFrame(columns=[
            "document_id", "symbol", "published_at", "scored_at",
            "model_name", "model_version",
            "sentiment_positive", "sentiment_negative", "sentiment_neutral",
            "sentiment_score",
        ])

    texts = docs_df["text"].fillna("").tolist()
    pipe = _get_pipeline()
    scored_at = pd.Timestamp.now(tz="UTC")
    rows = []

    for i in range(0, len(texts), batch_size):
        batch_texts = texts[i : i + batch_size]
        batch_docs = docs_df.iloc[i : i + batch_size]
        results = pipe(batch_texts, truncation=True, max_length=512)

        for doc_row, label_scores in zip(batch_docs.itertuples(), results):
            pos = next((s["score"] for s in label_scores if s["label"] == "positive"), 0.0)
            neg = next((s["score"] for s in label_scores if s["label"] == "negative"), 0.0)
            neu = next((s["score"] for s in label_scores if s["label"] == "neutral"), 0.0)
            rows.append({
                "document_id": doc_row.document_id,
                "symbol": doc_row.symbol,
                "published_at": pd.to_datetime(doc_row.published_at, utc=True),
                "scored_at": scored_at,
                "model_name": MODEL_NAME,
                "model_version": MODEL_VERSION,
                "sentiment_positive": float(pos),
                "sentiment_negative": float(neg),
                "sentiment_neutral": float(neu),
                "sentiment_score": float(pos - neg),
            })

    result_df = pd.DataFrame(rows)
    SENTIMENT_SCORED_SCHEMA.validate(result_df)
    return result_df


def run_scoring(docs_df: pd.DataFrame | None = None, batch_size: int = 32) -> int:
    """Load unscored text_documents, score with FinBERT, write sentiment_scored/."""
    if docs_df is None:
        docs_df = lake.read_processed("text_documents")
        if docs_df.empty:
            logger.info("No text_documents in lake — nothing to score")
            return 0

        existing = lake.read_processed(DATASET)
        if not existing.empty:
            already_scored = set(existing["document_id"].unique())
            docs_df = docs_df[~docs_df["document_id"].isin(already_scored)]

    if docs_df.empty:
        logger.info("All documents already scored")
        return 0

    logger.info("Scoring %d documents with FinBERT", len(docs_df))
    scored = score_documents(docs_df, batch_size=batch_size)

    existing = lake.read_processed(DATASET)
    if not existing.empty:
        scored = pd.concat([existing, scored], ignore_index=True)

    scored = (
        scored.sort_values("scored_at")
        .drop_duplicates(subset=["document_id"], keep="last")
        .sort_values(["symbol", "published_at"])
        .reset_index(drop=True)
    )

    lake.write_processed(scored, dataset=DATASET)
    logger.info("sentiment_scored/ now has %d rows", len(scored))
    return len(docs_df)


if __name__ == "__main__":
    import sys
    if "--download" in sys.argv:
        print(f"Pre-caching {MODEL_NAME} from HuggingFace Hub...")
        _get_pipeline()
        print("Done. FinBERT is cached and ready for offline use.")
    else:
        n = run_scoring()
        print(f"Scored {n} documents.")
