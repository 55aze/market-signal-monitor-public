import json
import unittest
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd
import requests
from market_signal_monitor.events import extract_events
from market_signal_monitor.notion import Notion, event_properties
from market_signal_monitor.scanner import run, curve_snapshots, attach_curve, fetch_worker


def fixture():
    index = pd.date_range("2026-08-01", periods=8, freq="h", tz="UTC", name="time")
    bars = pd.DataFrame({"open": 10., "high": 12., "low": 9., "close": 11.}, index=index)
    calculated = bars.copy()
    for name in ("D", "A", "M", "blueUpperBand", "blueLowerBand", "yellowUpperBand", "yellowLowerBand"):
        calculated[name] = 1.
    calculated["DXDX"] = [0, 0, 1, 0, 1, 0, 0, 1]
    calculated["DBJGXC"] = [0, 0, 0, 1, 0, 1, 0, 0]
    return bars, calculated


INSTRUMENT = {"id": "QQQ", "exchange": "NASDAQ", "symbol": "QQQ"}


class LiveStore:
    def __init__(self, checkpoints=None):
        self.checkpoints = checkpoints or {}
        self.attempts, self.successes, self.failures = [], [], []

    def preflight(self):
        pass

    def load_live(self, key):
        return key, self.checkpoints.get(key)

    def live_attempt(self, page, at):
        self.attempts.append((page, at))

    def live_success(self, page, through, at, structure=None):
        self.successes.append((page, through, at, structure))

    def live_failure(self, page, status, error, at):
        self.failures.append((page, status, error, at))


def events(**kwargs):
    bars, calculated = fixture()
    return extract_events(bars, calculated, INSTRUMENT, "4H", "v1", bars.index[-1],
                          warmup=1, **kwargs)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        calendar_patch = patch('market_signal_monitor.scanner.has_close_policy', return_value=False)
        calendar_patch.start()
        self.addCleanup(calendar_patch.stop)

    def test_timeframe_history_limits_and_live_retention(self):
        bars, calculated = fixture()
        config = {"pine_sha256": "abc", "warmup": 1,
                  "timeframes": ["30m", "4H", "1D", "1W"],
                  "backfill_limits": {"30m": 10, "4H": 5, "1D": 5, "1W": 5},
                  "instruments": [INSTRUMENT]}
        bars = pd.concat([bars] * 5, ignore_index=True)
        bars.index = pd.date_range("2026-08-01", periods=len(bars), freq="h", tz="UTC")
        calculated = pd.concat([calculated] * 5, ignore_index=True)
        calculated.index = bars.index
        calculated["DXDX"] = 1
        calculated["DBJGXC"] = 1
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            history = run(config, mode="backfill", client=Mock(), fetcher=lambda *args: bars)
            override = run(config, mode="backfill", client=Mock(), fetcher=lambda *args: bars, limit=2)
            live = run(config, mode="live", client=Mock(), fetcher=lambda *args: bars,
                       since=bars.index[0].isoformat(), status_store=LiveStore())
        self.assertEqual([s["events"] for s in history["streams"]], [20, 10, 10, 10])
        self.assertEqual([s["events"] for s in override["streams"]], [4, 4, 4, 4])
        self.assertEqual([s["events"] for s in live["streams"]], [76] * 4)

    def test_live_fetch_crosses_hour_boundary(self):
        bars, calculated = fixture()
        bars.index = calculated.index = pd.date_range(
            "2026-09-04T22:00:00Z", periods=8, freq="h", name="time")
        calculated["DXDX"] = [0, 0, 0, 0, 0, 1, 1, 1]
        calculated["DBJGXC"] = 0
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["1H"],
                  "instruments": [INSTRUMENT, {"id": "BTCUSD", "exchange": "BITSTAMP", "symbol": "BTCUSD"}]}
        started = pd.Timestamp("2026-09-05T04:59:10Z")
        completed = [pd.Timestamp("2026-09-05T04:59:30Z"), pd.Timestamp("2026-09-05T05:00:02Z")]
        clock = Mock()
        clock.now.return_value = started

        def fetch(instrument, *args):
            clock.now.return_value = completed[0 if instrument["id"] == "QQQ" else 1]
            return bars.iloc[:-1] if instrument["id"] == "QQQ" else bars

        client = Mock()
        client.append.return_value = True
        with patch("market_signal_monitor.scanner.datetime", clock), patch(
                "market_signal_monitor.scanner.calculate", side_effect=lambda frame: calculated.loc[frame.index]):
            report = run(config, mode="live", since="2026-09-05T04:00:00Z",
                         fetcher=fetch, client=client, status_store=LiveStore())
        self.assertEqual(report["status"], "completed")
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["run_at"], started.isoformat())
        self.assertEqual([s["events"] for s in report["streams"]], [0, 1])
        event, = report["events"]
        self.assertEqual(event["symbol"], "BTCUSD")
        self.assertEqual(event["timestamp"], "2026-09-05T04:00:00+00:00")
        self.assertEqual(event["next_bar_at"], "2026-09-05T05:00:00+00:00")
        self.assertEqual(event["first_seen"], completed[1].isoformat())
        self.assertFalse(event["backfill"])
        client.append.assert_called_once_with(event)

    def test_live_checkpoint_advances_only_after_event_delivery(self):
        bars, calculated = fixture()
        client, store = Mock(), LiveStore()
        client.append.return_value = True
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"],
                  "instruments": [INSTRUMENT]}
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            report = run(config, mode="live", since=bars.index[0].isoformat(),
                         now=bars.index[-1], fetcher=lambda *args: bars,
                         client=client, status_store=store)
        self.assertEqual(report["status"], "completed")
        self.assertEqual(store.successes[0][1], bars.index[-2])
        self.assertEqual(store.successes[0][3]["values"]["Close"], 11.0)
        self.assertEqual(store.failures, [])
        self.assertEqual(client.append.call_count, len(report["events"]))

    def test_live_writes_structure_even_without_new_signal(self):
        bars, calculated = fixture()
        calculated["DXDX"] = 0
        calculated["DBJGXC"] = 0
        client, store = Mock(), LiveStore()
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"],
                  "instruments": [INSTRUMENT]}
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            report = run(config, mode="live", since=bars.index[0].isoformat(),
                         now=bars.index[-1], fetcher=lambda *args: bars,
                         client=client, status_store=store)
        self.assertEqual(report["events"], [])
        self.assertEqual(store.successes[0][1], bars.index[-2])
        self.assertEqual(store.successes[0][3]["channel_position"]["blue"], "Above")

    def test_live_rejects_history_that_lost_durable_checkpoint(self):
        bars, calculated = fixture()
        checkpoint = bars.index[0] - pd.Timedelta(hours=1)
        store = LiveStore({"QQQ:4H": checkpoint})
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"],
                  "instruments": [INSTRUMENT]}
        client = Mock()
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            report = run(config, mode="live", since=bars.index[0].isoformat(),
                         now=bars.index[-1], fetcher=lambda *args: bars,
                         client=client, status_store=store)
        self.assertEqual(report["status"], "partial_failure")
        self.assertIn("gap cannot be ruled out", report["errors"][0]["error"])
        self.assertEqual(store.failures[0][1], "Fetch Failed")
        client.append.assert_not_called()

    def test_live_write_failure_does_not_advance_checkpoint(self):
        bars, calculated = fixture()
        store, client = LiveStore(), Mock()
        client.append.side_effect = RuntimeError("unknown create outcome")
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"],
                  "instruments": [INSTRUMENT]}
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            report = run(config, mode="live", since=bars.index[0].isoformat(),
                         now=bars.index[-1], fetcher=lambda *args: bars,
                         client=client, status_store=store)
        self.assertEqual(report["status"], "partial_failure")
        self.assertEqual(store.successes, [])
        self.assertEqual(store.failures[0][1], "Write Failed")

    def test_fetch_worker_retries_transient_provider_failure(self):
        success = Mock(returncode=0, stdout='', stderr='')
        transient = Mock(returncode=2, stdout='', stderr='temporary websocket failure')
        with patch('market_signal_monitor.scanner.subprocess.run',
                   side_effect=[transient, success]) as execute, patch(
                   'market_signal_monitor.scanner.read_bars', return_value='bars'):
            self.assertEqual(fetch_worker(INSTRUMENT, '30m', 5000), 'bars')
        self.assertEqual(execute.call_count, 2)

    def test_fetch_worker_reports_bounded_provider_diagnostic(self):
        failed = Mock(returncode=2, stdout='', stderr='provider rejected symbol')
        with patch('market_signal_monitor.scanner.subprocess.run', return_value=failed):
            with self.assertRaisesRegex(RuntimeError,
                                        'failed after 3 attempts.*provider rejected symbol'):
                fetch_worker(INSTRUMENT, '30m', 5000)

    def test_excludes_latest_and_keeps_n_per_direction(self):
        result = events(backfill=True, limit=1)
        self.assertEqual([e["signal"] for e in result], ["Bottom", "Sell"])
        self.assertEqual([e["timestamp"] for e in result], ["2026-08-01T04:00:00+00:00", "2026-08-01T05:00:00+00:00"])

    def test_live_fixed_activation_and_identity_survives_backfill(self):
        history = events(backfill=True)
        live = events(since="2026-08-01T04:00:00Z")
        self.assertEqual(len(live), 2)
        self.assertEqual(live[0]["event_id"], history[2]["event_id"])
        self.assertFalse(live[0]["backfill"])
        self.assertEqual(live[0]["validation"], "Unvalidated")

    def test_rejects_naive_or_missing_activation(self):
        with self.assertRaises(ValueError): events()
        with self.assertRaises(ValueError): events(since="2026-08-01")

    def test_no_confirmation_from_future(self):
        bars, calculated = fixture()
        result = extract_events(bars, calculated, INSTRUMENT, "4H", "v1", bars.index[4],
                                warmup=1, backfill=True)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(pd.Timestamp(e["next_bar_at"]) <= bars.index[4] for e in result))

    def test_payload_is_append_only_and_does_not_touch_research(self):
        event = events(backfill=True)[0]
        props = event_properties(event, "ticker-id")
        self.assertNotIn("Working View", props)
        self.assertNotIn("Interpretation", props)
        self.assertEqual(props["Validation"]["select"]["name"], "Unvalidated")
        self.assertFalse(props["Surfaced"]["checkbox"])
        self.assertTrue(props["Backfill"]["checkbox"])
        self.assertEqual(json.loads("".join(x["text"]["content"] for x in props["Raw Payload"]["rich_text"])), event)

    def test_rerun_does_not_create_duplicate(self):
        client = Notion("test-only", "events", "tickers")
        event = events(backfill=True)[0]
        client.query = Mock(side_effect=[iter([]), iter([{"id": "ticker-id"}]), iter([{"id": "event-id"}])])
        client.request = Mock(return_value={"id": "created"})
        self.assertTrue(client.append(event))
        self.assertFalse(client.append(event))
        self.assertEqual(client.request.call_count, 1)

    def test_ambiguous_create_timeout_is_not_retried(self):
        session = Mock()
        session.headers = {}
        session.request.side_effect = requests.Timeout()
        client = Notion("test-only", "events", "tickers", session)
        with self.assertRaisesRegex(RuntimeError, "unknown"):
            client.request("POST", "pages", {})
        self.assertEqual(session.request.call_count, 1)

    def test_curve_alignment_and_no_future_context(self):
        bars, _ = fixture()
        frames = {}
        for name, value in (("US02Y", 4.), ("US05Y", 4.1), ("US10Y", 4.2), ("US30Y", 4.5)):
            frame = bars.copy()
            frame["close"] = value
            frames[name, "4H"] = frame
        curves = curve_snapshots(frames)
        event = events(backfill=True)[0]
        attach_curve(event, curves)
        self.assertAlmostEqual(event["curve"]["2s30s_bp"], 50.)
        self.assertLessEqual(pd.Timestamp(event["curve"]["available_at"]), pd.Timestamp(event["next_bar_at"]))
        self.assertEqual(event["curve"]["bar_timestamp"], event["timestamp"])

    def test_partial_fetch_failure_is_not_no_signals(self):
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"],
                  "instruments": [INSTRUMENT, {"id": "BAD", "exchange": "X", "symbol": "BAD"}]}
        bars, calculated = fixture()
        def fetch(instrument, *args):
            if instrument["id"] == "BAD": raise RuntimeError("unreachable")
            return bars
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            report = run(config, fetcher=fetch, now=bars.index[-1])
        self.assertEqual(report["status"], "partial_failure")
        self.assertEqual(len(report["events"]), 4)
        self.assertEqual(report["streams"][1]["status"], "failed")

    def test_dry_run_never_writes_even_with_client(self):
        bars, calculated = fixture()
        config = {"pine_sha256": "abc", "warmup": 1, "timeframes": ["4H"], "instruments": [INSTRUMENT]}
        client = Mock()
        with patch("market_signal_monitor.scanner.calculate", return_value=calculated):
            report = run(config, client=client, fetcher=lambda *args: bars, now=bars.index[-1])
        self.assertEqual(report["status"], "completed")
        client.preflight.assert_not_called()
        client.append.assert_not_called()


if __name__ == "__main__":
    unittest.main()
