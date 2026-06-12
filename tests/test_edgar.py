"""Tests for the EDGAR filing ingestor (submissions API)."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pandas as pd

from quant.ingest.edgar import (
    _fetch_filing_text,
    _iter_submissions,
    _load_cik_map,
    _strip_html,
    fetch_filings,
    to_processed,
)

START = datetime(2020, 1, 1, tzinfo=timezone.utc)
END = datetime(2024, 1, 1, tzinfo=timezone.utc)


# ── _strip_html ────────────────────────────────────────────────────────────────

class TestStripHtml:
    def test_strips_tags(self):
        assert _strip_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_decodes_entities(self):
        assert _strip_html("AT&amp;T &lt;Corp&gt;") == "AT&T <Corp>"

    def test_collapses_whitespace(self):
        assert _strip_html("a  \n\t  b") == "a b"

    def test_caps_at_max_chars(self):
        assert len(_strip_html("x" * 100, max_chars=10)) == 10

    def test_empty_string(self):
        assert _strip_html("") == ""


# ── _load_cik_map ──────────────────────────────────────────────────────────────

class TestLoadCikMap:
    def _mock_client(self, payload: dict) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = payload
        client = MagicMock()
        client.get.return_value = resp
        return client

    def test_returns_ticker_to_cik_map(self):
        payload = {
            "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
            "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corp"},
        }
        with patch("quant.ingest.edgar.time.sleep"):
            result = _load_cik_map(self._mock_client(payload), MagicMock())
        assert result["AAPL"] == "320193"
        assert result["MSFT"] == "789019"

    def test_ticker_uppercased(self):
        payload = {"0": {"cik_str": 1, "ticker": "aapl", "title": "Apple"}}
        with patch("quant.ingest.edgar.time.sleep"):
            result = _load_cik_map(self._mock_client(payload), MagicMock())
        assert "AAPL" in result

    def test_http_failure_returns_empty(self):
        client = MagicMock()
        client.get.side_effect = Exception("timeout")
        with patch("quant.ingest.edgar.time.sleep"):
            result = _load_cik_map(client, MagicMock())
        assert result == {}


# ── _iter_submissions ──────────────────────────────────────────────────────────

def _submissions_payload(filings: list[dict], files: list[dict] | None = None) -> dict:
    """Build a minimal submissions API payload from a list of filing dicts."""
    if not filings:
        return {"filings": {"recent": {
            "accessionNumber": [], "filingDate": [], "form": [], "primaryDocument": []
        }, "files": files or []}}
    return {
        "filings": {
            "recent": {
                "accessionNumber": [f["acc"] for f in filings],
                "filingDate": [f["date"] for f in filings],
                "form": [f["form"] for f in filings],
                "primaryDocument": [f["doc"] for f in filings],
            },
            "files": files or [],
        }
    }


class TestIterSubmissions:
    def _client(self, *payloads) -> MagicMock:
        resps = []
        for p in payloads:
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json.return_value = p
            resps.append(r)
        client = MagicMock()
        client.get.side_effect = resps
        return client

    def test_returns_filings_in_date_range(self):
        payload = _submissions_payload([
            {"acc": "0001-23-001", "date": "2022-06-01", "form": "8-K", "doc": "8k.htm"},
            {"acc": "0001-23-002", "date": "2018-01-01", "form": "8-K", "doc": "8k.htm"},
        ])
        with patch("quant.ingest.edgar.time.sleep"):
            result = _iter_submissions(self._client(payload), "1", MagicMock(),
                                       {"8-K"}, START, END)
        assert len(result) == 1
        assert result[0]["accession"] == "0001-23-001"

    def test_filters_by_form_type(self):
        payload = _submissions_payload([
            {"acc": "0001-23-001", "date": "2022-01-01", "form": "8-K", "doc": "8k.htm"},
            {"acc": "0001-23-002", "date": "2022-01-02", "form": "SC 13G", "doc": "sc.htm"},
        ])
        with patch("quant.ingest.edgar.time.sleep"):
            result = _iter_submissions(self._client(payload), "1", MagicMock(),
                                       {"8-K"}, START, END)
        assert len(result) == 1
        assert result[0]["form"] == "8-K"

    def test_paginates_older_files(self):
        main = _submissions_payload(
            [{"acc": "0001-23-001", "date": "2022-01-01", "form": "8-K", "doc": "8k.htm"}],
            files=[{"name": "CIK0001-submissions-001.json", "date": "2021-01-01"}],
        )
        older = _submissions_payload([
            {"acc": "0001-21-001", "date": "2021-06-01", "form": "8-K", "doc": "8k.htm"},
        ])
        with patch("quant.ingest.edgar.time.sleep"):
            result = _iter_submissions(self._client(main, older), "1", MagicMock(),
                                       {"8-K"}, START, END)
        assert len(result) == 2

    def test_stops_pagination_when_file_predates_start(self):
        main = _submissions_payload(
            [],
            files=[{"name": "CIK0001-submissions-001.json", "date": "2015-01-01"}],
        )
        client = self._client(main)
        with patch("quant.ingest.edgar.time.sleep"):
            result = _iter_submissions(client, "1", MagicMock(), {"8-K"}, START, END)
        assert result == []
        # Should not have fetched the old supplemental file
        assert client.get.call_count == 1

    def test_http_failure_returns_empty(self):
        client = MagicMock()
        client.get.side_effect = Exception("timeout")
        with patch("quant.ingest.edgar.time.sleep"):
            result = _iter_submissions(client, "1", MagicMock(), {"8-K"}, START, END)
        assert result == []

    def test_filed_at_is_tz_aware(self):
        payload = _submissions_payload([
            {"acc": "0001-23-001", "date": "2022-06-01", "form": "8-K", "doc": "8k.htm"},
        ])
        with patch("quant.ingest.edgar.time.sleep"):
            result = _iter_submissions(self._client(payload), "1", MagicMock(),
                                       {"8-K"}, START, END)
        assert result[0]["filed_at"].tzinfo is not None


# ── _fetch_filing_text ─────────────────────────────────────────────────────────

class TestFetchFilingText:
    CIK = "320193"
    ACC = "0000320193-24-000039"
    DOC = "aapl-20240101.htm"

    def _client(self, status: int, body: str) -> MagicMock:
        resp = MagicMock()
        resp.status_code = status
        resp.raise_for_status = MagicMock(
            side_effect=None if status < 400 else Exception(f"HTTP {status}")
        )
        resp.text = body
        client = MagicMock()
        client.get.return_value = resp
        return client

    def test_returns_stripped_text(self):
        client = self._client(200, "<p>Apple reports record earnings.</p>")
        with patch("quant.ingest.edgar.time.sleep"):
            result = _fetch_filing_text(client, self.CIK, self.ACC, self.DOC, MagicMock())
        assert result == "Apple reports record earnings."

    def test_constructs_correct_url(self):
        client = self._client(200, "<p>text</p>")
        with patch("quant.ingest.edgar.time.sleep"):
            _fetch_filing_text(client, self.CIK, self.ACC, self.DOC, MagicMock())
        called_url = client.get.call_args[0][0]
        assert called_url == (
            "https://www.sec.gov/Archives/edgar/data"
            "/320193/000032019324000039/aapl-20240101.htm"
        )

    def test_empty_primary_doc_returns_empty(self):
        client = MagicMock()
        result = _fetch_filing_text(client, self.CIK, self.ACC, "", MagicMock())
        assert result == ""
        client.get.assert_not_called()

    def test_429_sleeps_and_returns_empty(self):
        client = self._client(429, "")
        mock_logger = MagicMock()
        with patch("quant.ingest.edgar.time.sleep") as mock_sleep:
            result = _fetch_filing_text(client, self.CIK, self.ACC, self.DOC, mock_logger)
        assert result == ""
        # Should sleep 60s in addition to the rate-limit sleep
        sleep_args = [c.args[0] for c in mock_sleep.call_args_list]
        assert 60 in sleep_args
        mock_logger.warning.assert_called()

    def test_http_error_returns_empty(self):
        client = self._client(500, "")
        with patch("quant.ingest.edgar.time.sleep"):
            result = _fetch_filing_text(client, self.CIK, self.ACC, self.DOC, MagicMock())
        assert result == ""

    def test_text_capped_at_max_chars(self):
        client = self._client(200, "<p>" + "x" * 50_000 + "</p>")
        with patch("quant.ingest.edgar.time.sleep"):
            result = _fetch_filing_text(client, self.CIK, self.ACC, self.DOC,
                                        MagicMock(), max_chars=100)
        assert len(result) == 100


# ── fetch_filings ──────────────────────────────────────────────────────────────

class TestFetchFilings:
    def _run(
        self,
        cik_map: dict,
        filings_by_symbol: dict,
        doc_text: str = "Filing body text.",
    ) -> pd.DataFrame:
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)

        with (
            patch("quant.ingest.edgar.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.edgar.httpx.Client", return_value=mock_client),
            patch("quant.ingest.edgar.time.sleep"),
            patch("quant.ingest.edgar._load_cik_map", return_value=cik_map),
            patch("quant.ingest.edgar._iter_submissions",
                  side_effect=lambda client, cik, logger, forms, start, end:
                      filings_by_symbol.get(cik, [])),
            patch("quant.ingest.edgar._fetch_filing_text", return_value=doc_text),
        ):
            return fetch_filings.fn(["AAPL"], START, END)

    def _filing(self, acc: str = "0001-23-001") -> dict:
        return {
            "accession": acc,
            "filed_at": pd.Timestamp("2022-06-01", tz="UTC"),
            "form": "8-K",
            "primary_doc": "8k.htm",
        }

    def test_returns_row_per_filing(self):
        df = self._run({"AAPL": "320193"}, {"320193": [self._filing()]})
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "AAPL"
        assert df.iloc[0]["text"] == "Filing body text."
        assert df.iloc[0]["form_type"] == "8-K"

    def test_unknown_ticker_skips_with_warning(self):
        df = self._run({}, {})  # empty CIK map
        assert df.empty

    def test_empty_cik_map_returns_empty_df(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        with (
            patch("quant.ingest.edgar.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.edgar.httpx.Client", return_value=mock_client),
            patch("quant.ingest.edgar.time.sleep"),
            patch("quant.ingest.edgar._load_cik_map", return_value={}),
        ):
            df = fetch_filings.fn(["AAPL"], START, END)
        assert df.empty

    def test_multiple_filings_all_returned(self):
        df = self._run(
            {"AAPL": "320193"},
            {"320193": [self._filing(f"acc-{i}") for i in range(5)]},
        )
        assert len(df) == 5

    def test_empty_text_warning_logged(self):
        mock_logger = MagicMock()
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        with (
            patch("quant.ingest.edgar.get_run_logger", return_value=mock_logger),
            patch("quant.ingest.edgar.httpx.Client", return_value=mock_client),
            patch("quant.ingest.edgar.time.sleep"),
            patch("quant.ingest.edgar._load_cik_map", return_value={"AAPL": "320193"}),
            patch("quant.ingest.edgar._iter_submissions",
                  return_value=[self._filing()]),
            patch("quant.ingest.edgar._fetch_filing_text", return_value=""),
        ):
            fetch_filings.fn(["AAPL"], START, END)
        warning_calls = " ".join(str(c) for c in mock_logger.warning.call_args_list)
        assert "empty text" in warning_calls


# ── to_processed ───────────────────────────────────────────────────────────────

class TestToProcessed:
    def _make_df(self, n: int = 2) -> pd.DataFrame:
        return pd.DataFrame({
            "document_id": [f"id-{i}" for i in range(n)],
            "source": ["edgar"] * n,
            "symbol": ["AAPL"] * n,
            "form_type": ["8-K"] * n,
            "published_at": pd.to_datetime(
                [f"2023-0{i+1}-01" for i in range(n)], utc=True
            ),
            "ingested_at": pd.Timestamp.now(tz="UTC"),
            "text": [f"Content {i}" for i in range(n)],
            "accession_number": [f"acc-{i}" for i in range(n)],
            "url": [f"https://sec.gov/{i}" for i in range(n)],
        })

    def test_empty_input_returns_zero(self):
        with patch("quant.ingest.edgar.lake.read_processed", return_value=pd.DataFrame()):
            assert to_processed.fn(pd.DataFrame()) == 0

    def test_empty_text_rows_dropped(self):
        df = self._make_df(2)
        df.at[0, "text"] = "   "
        with (
            patch("quant.ingest.edgar.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.edgar.lake.read_processed", return_value=pd.DataFrame()),
            patch("quant.ingest.edgar.lake.write_processed"),
        ):
            assert to_processed.fn(df) == 1

    def test_deduplicates_by_document_id(self):
        df = self._make_df(2)
        df.at[1, "document_id"] = "id-0"
        with (
            patch("quant.ingest.edgar.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.edgar.lake.read_processed", return_value=pd.DataFrame()),
            patch("quant.ingest.edgar.lake.write_processed") as mock_write,
        ):
            to_processed.fn(df)
        assert len(mock_write.call_args[0][0]) == 1

    def test_merge_with_existing_lake(self):
        df = self._make_df(2)
        existing = self._make_df(1)
        existing.at[0, "document_id"] = "existing-id"
        with (
            patch("quant.ingest.edgar.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.edgar.lake.read_processed", return_value=existing),
            patch("quant.ingest.edgar.lake.write_processed") as mock_write,
        ):
            n = to_processed.fn(df)
        written = mock_write.call_args[0][0]
        assert n == 3
        assert "existing-id" in written["document_id"].values

    def test_newer_ingested_at_wins_on_duplicate(self):
        old = self._make_df(1)
        old.at[0, "ingested_at"] = pd.Timestamp("2023-01-01", tz="UTC")
        new = self._make_df(1)
        new.at[0, "ingested_at"] = pd.Timestamp("2023-06-01", tz="UTC")
        new.at[0, "text"] = "Updated content"
        with (
            patch("quant.ingest.edgar.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.edgar.lake.read_processed", return_value=old),
            patch("quant.ingest.edgar.lake.write_processed") as mock_write,
        ):
            to_processed.fn(new)
        written = mock_write.call_args[0][0]
        assert len(written) == 1
        assert written.iloc[0]["text"] == "Updated content"
