import unittest
from unittest.mock import Mock, patch
from test_delivery import fixture, LiveStore, INSTRUMENT
from market_signal_monitor.scanner import run
from market_signal_monitor.state_engine import seed


class ScannerStateTests(unittest.TestCase):
    def config(self):
        return {'pine_sha256':'abc','warmup':1,'timeframes':['4H'],
                'instruments':[INSTRUMENT], 'ticker_engine':{'enabled':True}}

    def test_state_checkpoint_is_separate_and_failure_is_reported(self):
        bars, calculated = fixture()
        calculated['DXDX'] = calculated['DBJGXC'] = 0
        baseline = seed({'Ticker':'QQQ','Stage':'Developing','Direction':'Bottom','Origin TF':'4H',
                         'date:Origin Event At:start':bars.index[0].isoformat(),
                         'date:Last Origin Bar:start':bars.index[1].isoformat()})
        store = Mock(); store.load.return_value = baseline
        client = Mock(); raw = LiveStore()
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), patch(
                'market_signal_monitor.scanner.has_close_policy', return_value=False):
            report = run(self.config(), mode='live', since=bars.index[0].isoformat(),
                         fetcher=lambda *a:bars, client=client, status_store=raw, ticker_store=store)
        saved = store.save.call_args.args[1]
        self.assertEqual(saved['stage'],'Qualified')
        self.assertEqual(saved['streak'],5)
        self.assertEqual(report['ticker_engine']['saved'],1)
        store.save.side_effect = RuntimeError('state PATCH failed')
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), patch(
                'market_signal_monitor.scanner.has_close_policy', return_value=False):
            report = run(self.config(), mode='live', since=bars.index[0].isoformat(),
                         fetcher=lambda *a:bars, client=client, status_store=raw, ticker_store=store)
        self.assertEqual(report['status'],'partial_failure')
        self.assertEqual(baseline['streak'],0)

    def test_dry_run_does_not_touch_ticker_store(self):
        bars, calculated = fixture()
        store = Mock()
        with patch('market_signal_monitor.scanner.calculate',return_value=calculated), patch(
                'market_signal_monitor.scanner.has_close_policy',return_value=False):
            run(self.config(),fetcher=lambda *a:bars,ticker_store=store)
        store.preflight.assert_not_called()
        store.save.assert_not_called()

    def test_raw_write_failure_prevents_state_save(self):
        bars, calculated = fixture()
        store, client = Mock(), Mock()
        store.load.return_value = seed({'Ticker':'QQQ'})
        client.append.side_effect = RuntimeError('raw write failed')
        with patch('market_signal_monitor.scanner.calculate',return_value=calculated), patch(
                'market_signal_monitor.scanner.has_close_policy',return_value=False):
            report=run(self.config(),mode='live',since=bars.index[0].isoformat(),
                fetcher=lambda *a:bars,client=client,status_store=LiveStore(),ticker_store=store)
        self.assertEqual(report['status'],'partial_failure')
        store.save.assert_not_called()
