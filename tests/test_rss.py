"""Tests for the RSS feed ingestor."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from quant.ingest.rss import _parse_pubdate, _parse_symbol, _tag, fetch_feeds, to_processed


class TestParseSymbol:
    def test_extracts_ticker_from_query_param(self):
        assert _parse_symbol("https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL") == "AAPL"

    def test_case_insensitive(self):
        assert _parse_symbol("https://example.com/feed?s=msft") == "MSFT"

    def test_falls_back_to_macro_when_no_s_param(self):
        assert _parse_symbol("https://feeds.example.com/rss") == "macro"

    def test_falls_back_to_macro_for_empty_string(self):
        assert _parse_symbol("") == "macro"


class TestParsePubdate:
    def test_valid_rfc2822_returns_utc_timestamp(self):
        result = _parse_pubdate("Mon, 06 Jun 2023 14:30:00 +0000")
        assert result is not None
        assert result.tzinfo is not None
        assert result.year == 2023

    def test_none_input_returns_none(self):
        assert _parse_pubdate(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_pubdate("") is None

    def test_invalid_format_returns_none(self):
        assert _parse_pubdate("not a date") is None

    def test_result_is_tz_aware(self):
        result = _parse_pubdate("Mon, 06 Jun 2023 14:30:00 GMT")
        assert result is not None
        assert result.tzinfo is not None


class TestTag:
    def test_extracts_simple_tag(self):
        assert _tag("title", "<title>Hello world</title>") == "Hello world"

    def test_returns_none_for_missing_tag(self):
        assert _tag("title", "<description>no title here</description>") is None

    def test_strips_whitespace(self):
        assert _tag("title", "<title>  padded  </title>") == "padded"

    def test_handles_tag_with_attributes(self):
        assert _tag("link", '<link rel="alternate">http://example.com</link>') == "http://example.com"


class TestFetchFeeds:
    RSS_XML = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Apple earnings beat</title>
    <description>AAPL reported strong results.</description>
    <link>https://example.com/aapl</link>
    <pubDate>Mon, 06 Jun 2023 14:30:00 +0000</pubDate>
  </item>
  <item>
    <title>Missing date item</title>
    <description>No pubDate here.</description>
    <link>https://example.com/nodate</link>
  </item>
</channel></rss>"""

    def _mock_client(self, xml: str) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.text = xml
        client = MagicMock()
        client.__enter__ = MagicMock(return_value=client)
        client.__exit__ = MagicMock(return_value=False)
        client.get.return_value = resp
        return client

    def test_valid_item_produces_row(self):
        mock_client = self._mock_client(self.RSS_XML)
        with (
            patch("quant.ingest.rss.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.rss.httpx.Client", return_value=mock_client),
        ):
            df = fetch_feeds.fn(["https://feeds.example.com?s=AAPL"])
        assert len(df) == 1
        assert df.iloc[0]["symbol"] == "AAPL"
        assert "Apple earnings beat" in df.iloc[0]["text"]

    def test_item_with_missing_pubdate_is_dropped(self):
        mock_client = self._mock_client(self.RSS_XML)
        with (
            patch("quant.ingest.rss.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.rss.httpx.Client", return_value=mock_client),
        ):
            df = fetch_feeds.fn(["https://feeds.example.com?s=AAPL"])
        assert len(df) == 1

    def test_all_items_missing_pubdate_logs_warning(self):
        bad_xml = "<rss><channel><item><title>A</title><link>http://a.com</link></item></channel></rss>"
        mock_logger = MagicMock()
        mock_client = self._mock_client(bad_xml)
        with (
            patch("quant.ingest.rss.get_run_logger", return_value=mock_logger),
            patch("quant.ingest.rss.httpx.Client", return_value=mock_client),
        ):
            fetch_feeds.fn(["https://example.com?s=AAPL"])
        warning_calls = " ".join(str(c) for c in mock_logger.warning.call_args_list)
        assert "all" in warning_calls.lower()

    def test_feed_fetch_failure_logs_warning_and_continues(self):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = Exception("timeout")
        mock_logger = MagicMock()
        with (
            patch("quant.ingest.rss.get_run_logger", return_value=mock_logger),
            patch("quant.ingest.rss.httpx.Client", return_value=mock_client),
        ):
            df = fetch_feeds.fn(["https://broken.example.com"])
        assert df.empty
        mock_logger.warning.assert_called()

    def test_no_items_in_feed_returns_empty(self):
        mock_client = self._mock_client("<rss><channel></channel></rss>")
        with (
            patch("quant.ingest.rss.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.rss.httpx.Client", return_value=mock_client),
        ):
            df = fetch_feeds.fn(["https://example.com"])
        assert df.empty

    def test_macro_symbol_when_no_s_param(self):
        xml = """<rss><channel><item>
            <title>Market news</title><description>Update.</description>
            <link>http://example.com/market</link>
            <pubDate>Mon, 06 Jun 2023 10:00:00 +0000</pubDate>
        </item></channel></rss>"""
        mock_client = self._mock_client(xml)
        with (
            patch("quant.ingest.rss.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.rss.httpx.Client", return_value=mock_client),
        ):
            df = fetch_feeds.fn(["https://example.com/no-symbol"])
        assert df.iloc[0]["symbol"] == "macro"

    def test_published_at_is_tz_aware(self):
        mock_client = self._mock_client(self.RSS_XML)
        with (
            patch("quant.ingest.rss.get_run_logger", return_value=MagicMock()),
            patch("quant.ingest.rss.httpx.Client", return_value=mock_client),
        ):
            df = fetch_feeds.fn(["https://feeds.example.com?s=AAPL"])
        assert df.iloc[0]["published_at"].tzinfo is not None


class TestRssToProcessed:
    def _make_df(self, n: int = 2) -> pd.DataFrame:
        return pd.DataFrame({
            "document_id": [f"rss-{i}" for i in range(n)],
            "source": ["rss_aapl"] * n,
            "symbol": ["AAPL"] * n,
            "form_type": [None] * n,
            "published_at": pd.to_datetime(
                [f"2023-0{i+1}-01" for i in range(n)], utc=True
            ),
            "ingested_at": pd.Timestamp.now(tz="UTC"),
            "text": [f"News item {i}" for i in range(n)],
            "accession_number": [None] * n,
            "url": [f"https://example.com/{i}" for i in range(n)],
        })

    def test_empty_input_returns_zero(self):
        assert to_processed.fn(pd.DataFrame()) == 0

    def test_merge_with_existing(self):
        df = self._make_df(2)
        existing = self._make_df(1)
        existing.at[0, "document_id"] = "existing-rss"

        with (
            patch("quant.ingest.rss.lake.read_processed", return_value=existing),
            patch("quant.ingest.rss.lake.write_processed") as mock_write,
        ):
            n = to_processed.fn(df)

        written = mock_write.call_args[0][0]
        assert n == 3
        assert "existing-rss" in written["document_id"].values

    def test_deduplicates_by_document_id(self):
        df = self._make_df(2)
        df.at[1, "document_id"] = "rss-0"

        with (
            patch("quant.ingest.rss.lake.read_processed", return_value=pd.DataFrame()),
            patch("quant.ingest.rss.lake.write_processed") as mock_write,
        ):
            to_processed.fn(df)

        written = mock_write.call_args[0][0]
        assert len(written) == 1
