import copy
import unittest
from market_signal_monitor.state_engine import seed, advance, policy, acknowledge


def bar(i, close=12, tf='1D', **kw):
    return dict(timeframe=tf, timestamp=f'2026-09-{i:02d}T00:00:00Z',
                confirmed_at=f'2026-09-{i:02d}T23:00:00Z', close=close,
                low=min(close, 11), high=max(close, 13), blue_lower=9, blue_upper=11,
                **kw)


def initial(direction='Bottom', tf='1D'):
    return seed({'Ticker': 'TEST', 'Direction': direction, 'Origin TF': tf,
                 'Stage': 'Developing', 'Blue Streak Bars': 0,
                 'date:Origin Event At:start': '2026-09-01T00:00:00Z',
                 'date:Last Origin Bar:start': '2026-09-01T00:00:00Z'})


class EngineTests(unittest.TestCase):
    def run_bars(self, s, *bars):
        return advance(s, list(bars), [], '2026-10-01T00:00:00Z', policy())

    def test_acceptance_and_duplicate_close(self):
        s = self.run_bars(initial(), bar(2), bar(3), bar(4))
        self.assertEqual((s['stage'], s['streak']), ('Qualified', 3))
        self.assertEqual(self.run_bars(s, bar(4)), s)
        self.assertEqual(len(s['pending']), 1)

    def test_first_retest_then_two_close_failure_preserves_history(self):
        s = self.run_bars(initial(), bar(2), bar(3), bar(4), bar(5, 10))
        self.assertEqual(s['stage'], 'Opportunity')
        s = self.run_bars(s, bar(6, 8), bar(7, 8))
        self.assertEqual(s['stage'], 'Failed')
        self.assertEqual([e['to'] for e in s['history']],
                         ['Qualified', 'Opportunity', 'Weakening', 'Failed'])

    def test_wick_is_not_break(self):
        s = self.run_bars(initial(), bar(2), bar(3), bar(4))
        b = bar(5, 10); b['low'] = 1
        self.assertEqual(self.run_bars(s, b)['stage'], 'Opportunity')

    def test_missing_bounds_and_unconfirmed_bars_do_not_advance(self):
        b = bar(2); b['blue_lower'] = None
        s = self.run_bars(initial(), b, bar(3))
        self.assertEqual(s['streak'], 0)
        self.assertTrue(s['uncertainty'])
        future = bar(2); future['confirmed_at'] = '2027-01-01T00:00:00Z'
        self.assertEqual(self.run_bars(initial(), future), initial())

    def test_new_bar_breaks_streak_inside_blue(self):
        s = self.run_bars(initial(), bar(2), bar(3, 10), bar(4))
        self.assertEqual(s['streak'], 1)
        self.assertEqual(s['stage'], 'Developing')

    def test_sell_qualifies_but_does_not_create_short_opportunity(self):
        s = self.run_bars(initial('Sell'), bar(2, 8), bar(3, 8), bar(4, 8), bar(5, 10))
        self.assertEqual(s['stage'], 'Qualified')

    def test_lower_tf_opposite_preserves_origin(self):
        s = initial()
        e = dict(event_id='e1', ticker='TEST', timeframe='30m', signal='Sell',
                 timestamp='2026-09-02T00:00:00Z', confirmation_at='2026-09-02T00:30:00Z')
        s = advance(s, [], [e], '2026-09-03T00:00:00Z', policy())
        self.assertEqual((s['direction'], s['origin_tf']), ('Bottom', '1D'))
        self.assertEqual(s['highest_sell_tf'], '30m')
        self.assertEqual(advance(s, [], [e], '2026-09-03T00:00:00Z', policy()), s)

    def test_same_tf_opposite_keeps_old_setup_failure(self):
        s = self.run_bars(initial(), bar(2), bar(3), bar(4))
        e = dict(event_id='e2', ticker='TEST', timeframe='1D', signal='Sell',
                 timestamp='2026-09-05T00:00:00Z', confirmation_at='2026-09-05T23:00:00Z')
        s = advance(s, [], [e], '2026-09-06T00:00:00Z', policy())
        self.assertEqual((s['direction'], s['stage']), ('Sell', 'Signal'))
        self.assertIn('Failed', [e['to'] for e in s['history']])

    def test_ack_is_explicit_and_does_not_erase_history(self):
        s = self.run_bars(initial(), bar(2), bar(3), bar(4))
        original = copy.deepcopy(s)
        result = acknowledge(s, [s['pending'][0]['id']])
        self.assertFalse(result['pending'])
        self.assertEqual(result['history'], s['history'])
        self.assertEqual(s, original)

    def test_30m_needs_five_closes(self):
        s = self.run_bars(initial(tf='30m'), *(bar(i, tf='30m') for i in range(2, 6)))
        self.assertNotEqual(s['stage'], 'Qualified')
        self.assertEqual(self.run_bars(s, bar(6, tf='30m'))['stage'], 'Qualified')

    def test_no_hardcoded_expiry_or_trend_threshold(self):
        s = self.run_bars(initial(), bar(2), bar(3), bar(4), bar(5, 10), bar(6, 100))
        self.assertEqual(s['stage'], 'Opportunity')
        self.assertEqual(advance(s, [], [], '2027-01-01T00:00:00Z', policy())['stage'], 'Opportunity')

    def test_imported_baseline_no_input_preserves_stage(self):
        for stage in ['Developing', 'Qualified', 'Trend', 'No active setup']:
            s = seed({'Ticker':'T', 'Stage':stage, 'Blue Streak Bars':4})
            self.assertEqual(advance(s, [], [], '2026-09-21T00:00:00Z', policy()), s)

class PipelineTests(unittest.TestCase):
    def test_origin_gap_freezes_state(self):
        from market_signal_monitor.state_pipeline import process_ticker
        s = initial()
        b = bar(3); b['snapshot'] = {}; b['value_unit'] = 'Price'
        out = process_ticker(s, {'1D': {'bars':[b]}}, [], '2026-10-01T00:00:00Z')
        self.assertEqual(out['last_bar'], s['last_bar'])
        self.assertEqual(out['streak'], 0)
        self.assertTrue(out['uncertainty'])

    def test_fetch_failure_is_not_failed_setup(self):
        from market_signal_monitor.state_pipeline import process_ticker
        s = initial()
        out = process_ticker(s, {'1D': {'error':True}}, [], '2026-10-01T00:00:00Z')
        self.assertEqual(out['stage'], s['stage'])
        self.assertTrue(out['uncertainty'])

    def test_structural_acceptance_after_retest_silently_enters_background(self):
        s = initial()
        s = advance(s, [bar(2),bar(3),bar(4),bar(5,10),bar(6,12),bar(7,12),bar(8,12)], [],
                      '2026-10-01T00:00:00Z', policy())
        self.assertEqual(s['stage'], 'Trend')
        self.assertEqual(s['attention'], 'Background')
        self.assertNotIn('Trend', [e.get('to') for e in s['pending']])

    def test_theme_dedup_and_both_directions(self):
        from market_signal_monitor.report_packet import theme_evidence
        states = []
        for ticker, direction in [('SPX','Bottom'),('SPY','Bottom'),('US02Y','Sell'),('US10Y','Sell')]:
            s = seed({'Ticker':ticker})
            s['lineage'] = [{'event_id':ticker,'signal':direction,'timeframe':'1D',
                             'confirmation_at':'2026-09-20T00:00:00Z'}]
            states.append(s)
        out = theme_evidence(states, {s['ticker']:['T'] for s in states},
            {'SPX':'US-large','SPY':'US-large','US02Y':'2y','US10Y':'10y'},
            '2026-09-19T00:00:00Z','2026-09-21T00:00:00Z')[0]
        self.assertEqual(out['directions']['Bottom']['ticker_breadth'], 2)
        self.assertEqual(out['directions']['Bottom']['independent_exposure_breadth'], 1)
        self.assertEqual(out['directions']['Sell']['independent_exposure_breadth'], 2)

    def test_packet_is_quiet_without_changes(self):
        from market_signal_monitor.report_packet import packet
        self.assertFalse(packet([initial()], now='2026-09-21T00:00:00Z',
                               since='2026-09-20T00:00:00Z')['should_report'])

class MorePipelineTests(unittest.TestCase):
    def test_new_opposite_signal_not_delayed_until_weekly_origin_due(self):
        from market_signal_monitor.state_pipeline import process_ticker
        s = initial()
        e = dict(event_id='weekly-sell',ticker='TEST',signal='Sell',timeframe='1W',
                 timestamp='2026-09-02T00:00:00Z',confirmation_at='2026-09-09T00:00:00Z')
        out = process_ticker(s, {}, [e], '2026-09-10T00:00:00Z')
        self.assertEqual(out['direction'],'Sell')
        self.assertEqual(out['origin_tf'],'1W')

    def test_identical_data_gap_not_repeated(self):
        from market_signal_monitor.state_pipeline import process_ticker
        s = process_ticker(initial(), {'1D':{'error':True}}, [], '2026-09-21T00:00:00Z')
        out = process_ticker(s, {'1D':{'error':True}}, [], '2026-09-21T06:00:00Z')
        self.assertEqual(s['pending'],out['pending'])

class LateSignalTests(unittest.TestCase):
    def test_late_dominant_signal_does_not_rewrite_confirmed_success(self):
        s=advance(initial(),[bar(2),bar(3),bar(4)],[],'2026-10-01T00:00:00Z')
        e=dict(event_id='late',ticker='TEST',signal='Sell',timeframe='1D',
               timestamp='2026-09-02T00:00:00Z',confirmation_at='2026-09-02T23:00:00Z')
        out=advance(s,[],[e],'2026-10-01T00:00:00Z')
        self.assertEqual(out['stage'],'Qualified')
        self.assertTrue(out['uncertainty'])
