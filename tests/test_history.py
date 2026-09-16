import unittest
from unittest.mock import Mock, patch
import numpy as np
import pandas as pd
from market_signal_monitor.indicator import calculate
from market_signal_monitor.history import dependency_masks, history_evidence, POLICY
from market_signal_monitor.events import extract_events
from market_signal_monitor.scanner import run
from test_delivery import LiveStore


def bars_for(count, period=24):
    t = np.arange(count)
    close = 100 + 6 * np.sin(t * 2 * np.pi / period) + .015 * t
    return pd.DataFrame(dict(open=close, high=close + 1, low=close - 1, close=close),
                        index=pd.date_range('2010-01-01', periods=count, freq='D', tz='UTC'))


class HistoryTests(unittest.TestCase):
    def test_readiness_depends_on_crossings_not_500_bars(self):
        bars = bars_for(160)
        masks = dependency_masks(calculate(bars))
        self.assertTrue(masks.iloc[-1].all())
        self.assertTrue((masks.any(axis=1).to_numpy().nonzero()[0][0]) < 100)
        monotonic = bars_for(600)
        monotonic['close'] = np.arange(600) + 100.
        monotonic['high'] = monotonic.close + 1
        monotonic['low'] = monotonic.close - 1
        self.assertFalse(dependency_masks(calculate(monotonic)).any(axis=None))

    def test_future_bars_cannot_change_prior_readiness(self):
        bars = bars_for(200)
        short = dependency_masks(calculate(bars.iloc[:100]))
        full = dependency_masks(calculate(bars)).iloc[:100]
        pd.testing.assert_frame_equal(short, full)

    def test_seed_weight_matches_actual_ema_seed_perturbation(self):
        bars = bars_for(80)
        changed = bars.copy()
        changed.iloc[0, changed.columns.get_loc('close')] += 10
        a, b = calculate(bars), calculate(changed)
        evidence = history_evidence(bars, a, dependency_masks(a), bars.index[-1], 5000)
        for n in (20, 50, 100, 200):
            self.assertAlmostEqual(b.iloc[-1][f'ema{n}'] - a.iloc[-1][f'ema{n}'],
                                   10 * evidence['ema_initial_seed_weight'][str(n)], places=10)
        self.assertFalse(evidence['full_listing_history_verified'])

    def test_short_history_writes_structure_with_honest_signal_warning(self):
        bars = bars_for(8)
        instrument = {'id': 'YOUNG', 'exchange': 'X', 'symbol': 'YOUNG'}
        config = dict(pine_sha256='abc', warmup=500, history_policy=POLICY,
                      timeframes=['1W'], instruments=[instrument])
        store, client = LiveStore(), Mock()
        report = run(config, mode='live', since=bars.index[0].isoformat(),
                     fetcher=lambda *args: bars, status_store=store, client=client)
        self.assertEqual(report['errors'], [])
        self.assertEqual(report['streams'][0]['status'], 'fetched_and_computed')
        self.assertEqual(report['warnings'][0]['kind'], 'signal_history_unready')
        history = store.successes[0][3]['history']
        self.assertTrue(history['structure_computable'])
        self.assertEqual(history['returned_bars'], 8)
        self.assertEqual(history['requested_bars'], 5000)
        self.assertEqual(report['events'], [])
        client.append.assert_not_called()

    def test_existing_event_identity_unchanged_when_ready(self):
        bars = bars_for(180)
        calc = calculate(bars)
        masks = dependency_masks(calc)
        # Inject an emission only to isolate the unchanged event identity contract.
        calc['DXDX'] = 0
        calc['DBJGXC'] = 0
        calc.iloc[-3, calc.columns.get_loc('DXDX')] = 1
        args = (bars, calc, {'id': 'T', 'exchange': 'X', 'symbol': 'T'},
                '1D', 'same-version', bars.index[-1])
        old = extract_events(*args, backfill=True, warmup=1)
        new = extract_events(*args, backfill=True, warmup=0, readiness=masks)
        self.assertEqual([e['event_id'] for e in old], [e['event_id'] for e in new])

    def test_single_unconfirmed_row_reports_no_confirmed_bar(self):
        bars = bars_for(1)
        config = dict(pine_sha256='abc', history_policy=POLICY, timeframes=['1W'],
                      instruments=[{'id': 'T', 'exchange': 'X', 'symbol': 'T'}])
        report = run(config, fetcher=lambda *args: bars)
        self.assertEqual(report['errors'], [])
        self.assertIn('No confirmed bar', report['warnings'][0]['warning'])

    def test_backfill_migrates_empty_legacy_manifest_without_moving_window(self):
        from market_signal_monitor.backfill import run_backfill
        from test_backfill import Store
        bars = bars_for(160)
        config = dict(pine_sha256='abc', warmup=500, timeframes=['1D'],
                      instruments=[{'id': 'T', 'exchange': 'X', 'symbol': 'T'}],
                      backfill_months={'1D': 1}, backfill_limits={'1D': 5})
        store, client = Store(), Mock()
        client.append.return_value = True
        legacy = run_backfill(config, client=client, fetcher=lambda *args: bars,
                             selected=['T'], now=bars.index[-1], store=store)
        self.assertEqual(legacy['streams'][0]['status'], 'Insufficient History')
        old_start, old_end = store.state['start'], store.state['end']
        config['history_policy'] = POLICY
        new = run_backfill(config, client=client, fetcher=lambda *args: bars,
                          selected=['T'], now=bars.index[-1], store=store)
        self.assertEqual(new['errors'], [])
        self.assertEqual(new['streams'][0]['status'], 'Complete')
        self.assertEqual((store.state['start'], store.state['end']), (old_start, old_end))
        self.assertEqual(store.state['history']['returned_bars'], 160)
        self.assertEqual(store.state['history_policy'], POLICY)

    def test_backfill_confirmation_metadata_migrates_but_source_change_rejected(self):
        import copy
        from market_signal_monitor.backfill import run_backfill
        from test_backfill import Store
        bars = bars_for(160)
        config = dict(pine_sha256='abc', warmup=500, history_policy=POLICY,
                      timeframes=['1D'], instruments=[{'id': 'T', 'exchange': 'X', 'symbol': 'T'}],
                      backfill_months={'1D': 1}, backfill_limits={'1D': 5})
        store, client = Store(), Mock()
        client.append.return_value = True
        run_backfill(config, client=client, fetcher=lambda *args: bars,
                     selected=['T'], now=bars.index[-1], store=store)
        old_ids = [e['event_id'] for e in store.state['manifest']]
        config['instruments'][0]['confirmation'] = {'kind': 'calendar', 'calendar': 'NASDAQ', 'id': 'test-confirmation'}
        # Keep the same returned confirmations to isolate plan metadata migration.
        with patch('market_signal_monitor.backfill.has_close_policy', return_value=False):
            migrated = run_backfill(config, client=client, fetcher=lambda *args: bars,
                                   selected=['T'], now=bars.index[-1], store=store)
        self.assertEqual(migrated['errors'], [])
        self.assertEqual(old_ids, [e['event_id'] for e in store.state['manifest']])
        self.assertEqual(store.state['plan']['instrument'], config['instruments'][0])
        before = copy.deepcopy(store.state)
        config['instruments'][0]['exchange'] = 'DIFFERENT'
        rejected = run_backfill(config, client=client, fetcher=lambda *args: bars,
                                selected=['T'], now=bars.index[-1], store=store)
        self.assertEqual(rejected['status'], 'partial_failure')
        self.assertEqual(store.state, before)
