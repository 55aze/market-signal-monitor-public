import unittest
from unittest.mock import Mock, patch

import pandas as pd

from market_signal_monitor.freshness import assess_freshness
from market_signal_monitor.scanner import run


SSE = {"id": "588000", "exchange": "SSE", "symbol": "588000", "extended_session": False}
BTC = {
    "id": "BTCUSD", "exchange": "BITSTAMP", "symbol": "BTCUSD",
    "confirmation": {"type": "continuous_interval", "id": "btc-v1",
                     "gap_schedule": "continuous_24_7"},
}
US02Y = {"id": "US02Y", "exchange": "TVC", "symbol": "US02Y"}


class FreshnessTests(unittest.TestCase):
    def test_sse_daily_current_after_close(self):
        index = pd.to_datetime(["2026-09-09T01:30Z", "2026-09-10T01:30Z"], utc=True)
        result = assess_freshness(
            index, SSE, "1D", pd.Timestamp("2026-09-10T07:05Z"), index[-1],
            previous_through=index[0]
        )
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["expected_latest"], index[-1].isoformat())
        self.assertEqual(result["missing_count"], 0)

    def test_sse_daily_tail_stale_is_counted(self):
        index = pd.to_datetime(["2026-09-09T01:30Z"], utc=True)
        result = assess_freshness(
            index, SSE, "1D", pd.Timestamp("2026-09-10T07:05Z"), index[-1],
            previous_through=index[-1]
        )
        self.assertEqual(result["status"], "stale_tail")
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["missing_bar_opens"], ["2026-09-10T01:30:00+00:00"])

    def test_sse_intraday_interior_gap_is_not_tail_staleness(self):
        index = pd.to_datetime(
            ["2026-09-10T05:00Z", "2026-09-10T06:00Z", "2026-09-10T06:30Z"], utc=True
        )
        result = assess_freshness(
            index, SSE, "30m", pd.Timestamp("2026-09-10T07:05Z"), index[-1],
            previous_through=index[0]
        )
        self.assertEqual(result["status"], "interior_gap")
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["missing_bar_opens"], ["2026-09-10T05:30:00+00:00"])

    def test_sse_4h_matches_one_bar_per_day_tv_anchor(self):
        index = pd.to_datetime(["2026-09-09T01:30Z", "2026-09-10T01:30Z"], utc=True)
        result = assess_freshness(
            index, SSE, "4H", pd.Timestamp("2026-09-10T07:05Z"), index[-1],
            previous_through=index[0]
        )
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["expected_latest"], "2026-09-10T01:30:00+00:00")

    def test_btc_24x7_tail_and_interior_gap(self):
        current = pd.to_datetime(["2026-09-11T00:00Z", "2026-09-11T00:30Z"], utc=True)
        tail = assess_freshness(
            current, BTC, "30m", pd.Timestamp("2026-09-11T01:40Z"), current[-1],
            previous_through=current[0]
        )
        self.assertEqual(tail["status"], "stale_tail")
        self.assertEqual(tail["missing_bar_opens"], ["2026-09-11T01:00:00+00:00"])

        gap_index = pd.to_datetime(["2026-09-11T00:00Z", "2026-09-11T01:00Z"], utc=True)
        gap = assess_freshness(
            gap_index, BTC, "30m", pd.Timestamp("2026-09-11T01:40Z"), gap_index[-1],
            previous_through=gap_index[0]
        )
        self.assertEqual(gap["status"], "interior_gap")
        self.assertEqual(gap["missing_bar_opens"], ["2026-09-11T00:30:00+00:00"])

    def test_candidate_continuous_market_is_not_falsely_called_gap(self):
        brent = {
            "id": "BRENT", "exchange": "ICEEUR", "symbol": "BRN1!",
            "confirmation": {"type": "continuous_interval", "id": "candidate-v1"},
        }
        index = pd.to_datetime(["2026-09-11T00:00Z", "2026-09-11T04:00Z"], utc=True)
        result = assess_freshness(
            index, brent, "4H", pd.Timestamp("2026-09-11T12:00Z"), index[-1],
            previous_through=index[0]
        )
        self.assertEqual(result["status"], "unverifiable")
        self.assertEqual(result["verification"], "unsupported_continuous")

    def test_treasury_omitted_session_open_prefix_is_not_a_data_gap(self):
        index = pd.to_datetime([
            "2026-09-18T21:00Z", "2026-09-21T06:00Z",
            "2026-09-21T06:30Z", "2026-09-21T07:00Z"], utc=True)
        result = assess_freshness(
            index, US02Y, "30m", pd.Timestamp("2026-09-21T07:40Z"), index[-1],
            previous_through=index[0]
        )
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["missing_count"], 0)
        self.assertEqual(result["session_open_variance_count"], 12)
        self.assertEqual(result["session_open_variance_bar_opens"][0],
                         "2026-09-21T00:00:00+00:00")

    def test_treasury_hole_after_first_session_bar_remains_data_gap(self):
        index = pd.to_datetime([
            "2026-09-18T21:00Z", "2026-09-21T06:00Z",
            "2026-09-21T07:00Z"], utc=True)
        result = assess_freshness(
            index, US02Y, "30m", pd.Timestamp("2026-09-21T07:40Z"), index[-1],
            previous_through=index[0]
        )
        self.assertEqual(result["status"], "interior_gap")
        self.assertEqual(result["missing_count"], 1)
        self.assertEqual(result["missing_bar_opens"], ["2026-09-21T06:30:00+00:00"])

    def test_treasury_4h_omitted_reopen_bar_is_not_a_data_gap(self):
        index = pd.to_datetime([
            "2026-09-18T19:00Z", "2026-09-21T03:00Z"], utc=True)
        result = assess_freshness(
            index, US02Y, "4H", pd.Timestamp("2026-09-21T07:10Z"), index[-1],
            previous_through=index[0]
        )
        self.assertEqual(result["status"], "current")
        self.assertEqual(result["missing_count"], 0)
        self.assertEqual(result["session_open_variance_count"], 1)
        self.assertEqual(result["session_open_variance_bar_opens"],
                         ["2026-09-20T23:00:00+00:00"])


class Store:
    def __init__(self, checkpoint):
        self.checkpoint = checkpoint
        self.successes, self.failures = [], []

    def preflight(self):
        pass

    def load_live(self, key):
        return key, self.checkpoint

    def live_attempt(self, page, at):
        pass

    def live_success(self, page, through, at, structure=None):
        self.successes.append((page, through, structure))

    def live_failure(self, page, status, error, at):
        self.failures.append((page, status, error))


def generic_bars():
    index = pd.date_range("2026-09-10T00:00Z", periods=8, freq="h", name="time")
    return pd.DataFrame(
        {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0}, index=index
    )


def calculated(frame):
    result = frame.copy()
    for name in (
        "D", "A", "M", "ema20", "ema50", "ema100", "ema200",
        "blueUpperBand", "blueLowerBand", "yellowUpperBand", "yellowLowerBand"
    ):
        result[name] = 1.0
    result["DXDX"] = 0
    result["DBJGXC"] = 0
    return result


class ScannerFreshnessIntegrationTests(unittest.TestCase):
    def test_interior_gap_never_advances_checkpoint_or_emits(self):
        bars = generic_bars()
        store, client = Store(bars.index[0]), Mock()
        config = {
            "pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"],
            "instruments": [{"id": "TEST", "exchange": "X", "symbol": "TEST"}],
        }
        gap = {
            "status": "interior_gap", "verification": "exchange_calendar",
            "expected_latest": bars.index[-2].isoformat(),
            "actual_latest": bars.index[-2].isoformat(),
            "checkpoint": bars.index[0].isoformat(), "missing_count": 1,
            "missing_bar_opens": [bars.index[3].isoformat()], "missing_truncated": False,
        }
        with patch("market_signal_monitor.scanner.has_close_policy", return_value=False), \
             patch("market_signal_monitor.scanner.assess_freshness", return_value=gap), \
             patch("market_signal_monitor.scanner.calculate") as calc:
            report = run(
                config, mode="live", since=bars.index[0].isoformat(),
                fetcher=lambda *args: bars, client=client, status_store=store
            )
        self.assertEqual(report["status"], "completed_with_warnings")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"][0]["kind"], "data_gap")
        self.assertEqual(report["streams"][0]["status"], "skipped_data_gap")
        self.assertEqual(store.successes, [])
        self.assertEqual(store.failures[0][1], "Fetch Failed")
        self.assertIn("Expected confirmed bar gap", store.failures[0][2])
        client.append.assert_not_called()
        calc.assert_not_called()

    def test_stale_tail_keeps_last_known_structure_and_records_freshness(self):
        bars = generic_bars()
        store, client = Store(bars.index[0]), Mock()
        config = {
            "pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"],
            "instruments": [{"id": "TEST", "exchange": "X", "symbol": "TEST"}],
        }
        stale = {
            "status": "stale_tail", "verification": "exchange_calendar",
            "expected_latest": "2026-09-10T08:00:00+00:00",
            "actual_latest": bars.index[-2].isoformat(),
            "checkpoint": bars.index[0].isoformat(), "missing_count": 1,
            "missing_bar_opens": ["2026-09-10T08:00:00+00:00"], "missing_truncated": False,
        }
        with patch("market_signal_monitor.scanner.has_close_policy", return_value=False), \
             patch("market_signal_monitor.scanner.assess_freshness", return_value=stale), \
             patch("market_signal_monitor.scanner.calculate", side_effect=calculated):
            report = run(
                config, mode="live", since=bars.index[0].isoformat(),
                fetcher=lambda *args: bars, client=client, status_store=store
            )
        self.assertEqual(report["status"], "completed_with_warnings")
        self.assertEqual(report["warnings"][0]["kind"], "stale_tail")
        self.assertEqual(len(store.successes), 1)
        structure = store.successes[0][2]
        self.assertEqual(structure["freshness"]["status"], "stale_tail")
        self.assertEqual(structure["values"]["Close"], 11.0)


if __name__ == "__main__":
    unittest.main()
