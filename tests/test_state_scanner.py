import unittest
from unittest.mock import Mock, patch
from test_delivery import fixture, LiveStore, INSTRUMENT
from market_signal_monitor.scanner import run
from market_signal_monitor.state_engine import seed


class ScannerStateTests(unittest.TestCase):
    def config(self):
        return {'pine_sha256':'abc','warmup':1,'timeframes':['4H'],
                'instruments':[INSTRUMENT], 'theme_membership':{}, 'ticker_engine':{'enabled':True}}

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

    def test_one_provider_failure_does_not_block_other_ticker_or_packet(self):
        bars, calculated = fixture()
        calculated['DXDX'] = calculated['DBJGXC'] = 0
        config = self.config()
        config['instruments'] = [dict(INSTRUMENT, id='BAD', symbol='BAD'), INSTRUMENT]
        config['ticker_engine']['report_page'] = 'report-page'
        store, client, raw = Mock(), Mock(), LiveStore()
        store.rows = {'BAD': {}, 'QQQ': {}}
        store.load.side_effect = lambda ticker, now: seed({
            'Ticker':ticker, 'Stage':'Developing', 'Direction':'Bottom', 'Origin TF':'4H',
            'date:Origin Event At:start':bars.index[0].isoformat(),
            'date:Last Origin Bar:start':bars.index[1].isoformat()})
        def fetch(instrument, *args):
            if instrument['id'] == 'BAD':
                raise RuntimeError('provider timeout')
            return bars
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), \
             patch('market_signal_monitor.scanner.has_close_policy', return_value=False), \
             patch('market_signal_monitor.report_packet.delivery_receipt', return_value=[]), \
             patch('market_signal_monitor.report_packet.publish_packet') as publish:
            report = run(config, mode='live', since=bars.index[0].isoformat(), fetcher=fetch,
                         client=client, status_store=raw, ticker_store=store)
        saved = {call.args[0]:call.args[1] for call in store.save.call_args_list}
        self.assertEqual(saved['QQQ']['stage'], 'Qualified')
        self.assertEqual(saved['BAD']['stage'], 'Developing')
        self.assertEqual(saved['BAD']['last_bar'], bars.index[1].isoformat())
        self.assertTrue(saved['BAD']['uncertainty'])
        self.assertEqual(report['errors'][0]['kind'], 'provider_fetch')
        data = publish.call_args.args[2]
        self.assertEqual(data['coverage'], 'partial')
        self.assertEqual(data['operational_errors'][0]['stream'], 'BAD:4H')
        self.assertEqual(report['status'], 'partial_failure')

    def test_state_save_failure_does_not_block_next_ticker(self):
        bars, calculated = fixture()
        calculated['DXDX'] = calculated['DBJGXC'] = 0
        config = self.config()
        config['instruments'] = [dict(INSTRUMENT, id='BAD', symbol='BAD'), INSTRUMENT]
        store = Mock()
        store.load.side_effect = lambda ticker, now: seed({'Ticker':ticker})
        store.save.side_effect = [RuntimeError('PATCH failed'), None]
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), \
             patch('market_signal_monitor.scanner.has_close_policy', return_value=False):
            report = run(config, mode='live', since=bars.index[0].isoformat(),
                         fetcher=lambda *args:bars, client=Mock(), status_store=LiveStore(), ticker_store=store)
        self.assertEqual([c.args[0] for c in store.save.call_args_list], ['BAD', 'QQQ'])
        self.assertEqual(report['ticker_engine']['saved'], 1)
        self.assertEqual(report['errors'][0]['kind'], 'state_write_or_compute')

    def test_invalid_delivery_receipt_does_not_stop_scan_or_state_save(self):
        bars, calculated = fixture()
        calculated['DXDX'] = calculated['DBJGXC'] = 0
        config = self.config()
        config['ticker_engine']['report_page'] = 'report-page'
        store, client = Mock(), Mock()
        store.rows = {'QQQ': {}}
        store.load.return_value = seed({'Ticker':'QQQ'})
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), \
             patch('market_signal_monitor.scanner.has_close_policy', return_value=False), \
             patch('market_signal_monitor.report_packet.delivery_receipt',
                   side_effect=ValueError('bad receipt')), \
             patch('market_signal_monitor.report_packet.publish_packet'):
            report = run(config, mode='live', since=bars.index[0].isoformat(),
                         fetcher=lambda *args:bars, client=client,
                         status_store=LiveStore(), ticker_store=store)
        self.assertEqual(report['status'], 'completed_with_warnings')
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['warnings'][0]['kind'], 'receipt_invalid')
        store.save.assert_called_once()

    def test_receipt_write_failure_keeps_pending_but_market_state_advances(self):
        from market_signal_monitor.report_packet import DeliveryReceipt
        bars, calculated = fixture()
        calculated['DXDX'] = calculated['DBJGXC'] = 0
        config = self.config()
        config['ticker_engine']['report_page'] = 'report-page'
        state = seed({'Ticker':'QQQ','Stage':'Developing','Direction':'Bottom','Origin TF':'4H',
                      'date:Origin Event At:start':bars.index[0].isoformat(),
                      'date:Last Origin Bar:start':bars.index[1].isoformat()})
        state['pending'] = [{'id':'pending','source_event_id':'raw','ticker':'QQQ',
                             'kind':'RAW_SIGNAL','at':bars.index[0].isoformat()}]
        store, client = Mock(), Mock()
        store.load.return_value = state
        store.rows = {'QQQ':{}}
        client.mark_reported.side_effect = RuntimeError('ambiguous receipt write')
        receipt = DeliveryReceipt(['pending'], 'packet', bars.index[-1].isoformat())
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), \
             patch('market_signal_monitor.scanner.has_close_policy', return_value=False), \
             patch('market_signal_monitor.report_packet.delivery_receipt', return_value=receipt), \
             patch('market_signal_monitor.report_packet.publish_packet'):
            report = run(config, mode='live', since=bars.index[0].isoformat(),
                         fetcher=lambda *a:bars, client=client, status_store=LiveStore(), ticker_store=store)
        saved = store.save.call_args.args[1]
        self.assertEqual(saved['stage'], 'Qualified')
        self.assertIn('pending', [e['id'] for e in saved['pending']])
        self.assertEqual(report['errors'][0]['kind'], 'receipt_write')

    def test_unknown_time_ack_marks_only_raw_and_keeps_separate_confirmation_clock(self):
        from market_signal_monitor.report_packet import DeliveryReceipt
        bars, calculated = fixture()
        calculated['DXDX'] = calculated['DBJGXC'] = 0
        config = self.config()
        config['ticker_engine']['report_page'] = 'report-page'
        state = seed({'Ticker': 'QQQ'})
        state['pending'] = [dict(id=kind, source_event_id='raw', ticker='QQQ',
                                 kind=kind, at=bars.index[0].isoformat())
                            for kind in ('RAW_SIGNAL', 'HIGHER_TF_SIGNAL')]
        store, client = Mock(), Mock()
        store.load.return_value = state
        store.rows = {'QQQ': {}}
        receipt = DeliveryReceipt(['RAW_SIGNAL', 'HIGHER_TF_SIGNAL'], 'batch', None,
                                  'prior_complete_report', 'visible report')
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), \
             patch('market_signal_monitor.scanner.has_close_policy', return_value=False), \
             patch('market_signal_monitor.report_packet.delivery_receipt', return_value=receipt), \
             patch('market_signal_monitor.report_packet.publish_packet'):
            report = run(config, mode='live', since=bars.index[0].isoformat(),
                         fetcher=lambda *a: bars, client=client,
                         status_store=LiveStore(), ticker_store=store)
        client.mark_reported.assert_called_once_with('raw', None, 'batch')
        saved = store.save.call_args.args[1]
        self.assertEqual(saved['pending'], [])
        self.assertIsNone(saved['delivered'][0]['delivered_at'])
        self.assertEqual(saved['delivered'][0]['report_ref'], 'visible report')
        self.assertEqual(report['errors'], [])

    def test_packet_http_failure_retains_safe_diagnostics_and_pending(self):
        from market_signal_monitor.notion import NotionHTTPError
        bars, calculated = fixture()
        calculated['DXDX'] = calculated['DBJGXC'] = 0
        config = self.config()
        config['ticker_engine']['report_page'] = 'report-page'
        state = seed({'Ticker': 'QQQ'})
        state['pending'] = [dict(id='old', ticker='QQQ', kind='DATA_GAP',
                                 at=bars.index[0].isoformat())]
        store, client = Mock(), Mock()
        store.load.return_value = state
        store.rows = {'QQQ': {}}
        with patch('market_signal_monitor.scanner.calculate', return_value=calculated), \
             patch('market_signal_monitor.scanner.has_close_policy', return_value=False), \
             patch('market_signal_monitor.report_packet.delivery_receipt', return_value=[]), \
             patch('market_signal_monitor.report_packet.publish_packet',
                   side_effect=NotionHTTPError(400, 'PATCH', 'pages/private-id')):
            report = run(config, mode='live', since=bars.index[0].isoformat(),
                         fetcher=lambda *a: bars, client=client,
                         status_store=LiveStore(), ticker_store=store)
        diagnostics = report['packet_diagnostics']
        self.assertEqual(diagnostics['code'], 'notion_http')
        self.assertEqual(diagnostics['http_status'], 400)
        self.assertEqual(diagnostics['phase'], 'publish')
        self.assertNotIn('private-id', str(diagnostics))
        self.assertGreater(diagnostics['chars'], 0)
        self.assertIn('old', [e['id'] for e in store.save.call_args.args[1]['pending']])
