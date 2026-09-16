import unittest
from unittest.mock import Mock

import pandas as pd

from market_signal_monitor.freshness import scan_due
from market_signal_monitor.scanner import run


QQQ = {"id": "QQQ", "exchange": "NASDAQ", "symbol": "QQQ"}


class ScheduleTests(unittest.TestCase):
    def test_daily_waits_for_close_and_remains_due_until_checkpoint_advances(self):
        checkpoint = pd.Timestamp("2026-09-14T13:30Z")
        self.assertFalse(scan_due(QQQ, "1D", checkpoint, "2026-09-15T19:37Z"))
        self.assertTrue(scan_due(QQQ, "1D", checkpoint, "2026-09-15T20:07Z"))
        self.assertTrue(scan_due(QQQ, "1D", checkpoint, "2026-09-16T12:07Z"))

    def test_weekly_does_not_close_midweek(self):
        checkpoint = pd.Timestamp("2026-09-08T13:30Z")
        self.assertFalse(scan_due(QQQ, "1W", checkpoint, "2026-09-16T21:07Z"))
        self.assertTrue(scan_due(QQQ, "1W", checkpoint, "2026-09-18T20:07Z"))

    def test_half_day_and_winter_dst(self):
        checkpoint = pd.Timestamp("2026-11-25T14:30Z")
        self.assertFalse(scan_due(QQQ, "1D", checkpoint, "2026-11-26T22:07Z"))
        self.assertFalse(scan_due(QQQ, "1D", checkpoint, "2026-11-27T17:37Z"))
        self.assertTrue(scan_due(QQQ, "1D", checkpoint, "2026-11-27T18:07Z"))

    def test_4h_session_anchor(self):
        checkpoint = pd.Timestamp("2026-09-14T17:30Z")
        self.assertFalse(scan_due(QQQ, "4H", checkpoint, "2026-09-15T17:07Z"))
        self.assertTrue(scan_due(QQQ, "4H", checkpoint, "2026-09-15T17:37Z"))

    def test_bootstrap_unknown_calendar_and_30m_poll(self):
        self.assertTrue(scan_due(QQQ, "1W", None, "2026-09-16T12:07Z"))
        unknown = {"id": "X", "exchange": "X", "symbol": "X"}
        self.assertTrue(scan_due(unknown, "1W", "2026-09-14T00:00Z", "2026-09-16T12:07Z"))
        self.assertTrue(scan_due(QQQ, "30m", "2026-09-15T19:30Z", "2026-09-16T00:07Z"))

    def test_skip_has_no_fetch_or_status_write(self):
        store, client, fetcher = Mock(), Mock(), Mock()
        store.load_live.return_value = ("page", pd.Timestamp("2026-09-14T13:30Z"))
        report = run({"pine_sha256": "abc", "schedule_by_close": True,
                      "timeframes": ["1D"], "instruments": [QQQ]},
                     mode="live", since="2026-09-01T00:00Z", now="2026-09-15T19:37Z",
                     fetcher=fetcher, client=client, status_store=store)
        self.assertEqual(report["streams"][0]["status"], "skipped_not_due")
        fetcher.assert_not_called()
        store.live_attempt.assert_not_called()
        store.live_success.assert_not_called()
        client.append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
