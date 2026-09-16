import unittest
from unittest.mock import Mock, patch

import pandas as pd

from market_signal_monitor.scanner import run


class StatusSemanticsTests(unittest.TestCase):
    def setUp(self):
        calendar_patch = patch('market_signal_monitor.scanner.has_close_policy', return_value=False)
        calendar_patch.start()
        self.addCleanup(calendar_patch.stop)

    def test_insufficient_history_is_warning_not_failure(self):
        index = pd.date_range("2026-01-01", periods=8, freq="7D", tz="UTC", name="time")
        bars = pd.DataFrame({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0}, index=index)
        calculated = bars.copy()
        for name in ("D", "A", "M", "blueUpperBand", "blueLowerBand", "yellowUpperBand", "yellowLowerBand"):
            calculated[name] = 1.0
        calculated["DXDX"] = 0
        calculated["DBJGXC"] = 0
        config = {
            "pine_sha256": "abc",
            "warmup": 500,
            "timeframes": ["1W"],
            "instruments": [{"id": "YOUNG", "exchange": "NASDAQ", "symbol": "YOUNG"}],
        }
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            report = run(config, fetcher=lambda *args: bars, now=index[-1])
        self.assertEqual(report["status"], "completed_with_warnings")
        self.assertEqual(report["errors"], [])
        self.assertEqual(len(report["warnings"]), 1)
        self.assertEqual(report["warnings"][0]["kind"], "insufficient_history")
        self.assertEqual(report["streams"][0]["status"], "skipped_insufficient_history")

    def test_us_daily_future_open_candidate_uses_previous_bar_under_next_bar_policy(self):
        index = pd.DatetimeIndex([
            "2026-09-08T13:30:00Z", "2026-09-09T13:30:00Z", "2026-09-10T13:30:00Z"
        ], name="time")
        bars = pd.DataFrame({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0}, index=index)
        calculated = bars.copy()
        for name in ("D", "A", "M", "blueUpperBand", "blueLowerBand", "yellowUpperBand", "yellowLowerBand"):
            calculated[name] = 1.0
        calculated["DXDX"] = 0
        calculated["DBJGXC"] = 0
        instrument = {"id": "NVDA", "exchange": "NASDAQ", "symbol": "NVDA"}
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["1D"],
                  "instruments": [instrument]}
        clock = Mock()
        clock.now.return_value = pd.Timestamp("2026-09-10T12:33:00Z").to_pydatetime()
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated), \
             patch("market_signal_monitor.scanner.datetime", clock):
            report = run(config, fetcher=lambda *args: bars,
                         now=pd.Timestamp("2026-09-10T12:33:00Z"))
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["streams"][0]["confirmation_policy"], "next-bar")
        self.assertEqual(report["streams"][0]["latest_returned_bar"], index[-1].isoformat())
        self.assertEqual(report["streams"][0]["latest_processed_bar"], index[-2].isoformat())

    def test_future_bar_error_skips_stream_as_warning(self):
        index = pd.DatetimeIndex([
            "2026-09-10T11:30:00Z", "2026-09-10T12:00:00Z", "2026-09-10T13:30:00Z"
        ], name="time")
        bars = pd.DataFrame({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0}, index=index)
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["30m"],
                  "instruments": [{"id": "NVDA", "exchange": "NASDAQ", "symbol": "NVDA"}]}
        clock = Mock()
        clock.now.return_value = pd.Timestamp("2026-09-10T12:33:00Z").to_pydatetime()
        with patch("market_signal_monitor.scanner.datetime", clock), \
             patch("market_signal_monitor.scanner.calculate") as calculate:
            report = run(config, fetcher=lambda *args: bars,
                         now=pd.Timestamp("2026-09-10T12:33:00Z"))
        self.assertEqual(report["status"], "completed_with_warnings")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["warnings"], [{
            "stream": "NVDA:30m",
            "kind": "future_tail_skipped",
            "warning": "Provider returned a future bar timestamp",
        }])
        self.assertEqual(report["streams"], [{
            "stream": "NVDA:30m",
            "status": "skipped_future_bar",
            "warning": "Provider returned a future bar timestamp",
        }])
        calculate.assert_not_called()

    def test_real_fetch_failure_still_fails(self):
        config = {
            "pine_sha256": "abc",
            "warmup": 1,
            "timeframes": ["4H"],
            "instruments": [{"id": "BAD", "exchange": "X", "symbol": "BAD"}],
        }
        report = run(config, fetcher=lambda *args: (_ for _ in ()).throw(RuntimeError("unreachable")),
                     now=pd.Timestamp("2026-09-06T00:00:00Z"))
        self.assertEqual(report["status"], "partial_failure")
        self.assertEqual(report["warnings"], [])
        self.assertEqual(report["streams"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
