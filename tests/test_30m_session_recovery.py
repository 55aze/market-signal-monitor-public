"""Regression cases from native timestamps captured by Actions run 42654757461.

These are source timestamp samples, not invented OHLC. Full captures and safe
symbol_resolved metadata are retained in its session-recovery-evidence artifact.
"""
import unittest
import pandas as pd
from market_signal_monitor.confirmation import confirmation_times, policy_for
from market_signal_monitor.freshness import assess_freshness, _expected


class Recovery30mTests(unittest.TestCase):
    YIELDS = ('US02Y', 'US05Y', 'US10Y', 'US30Y')

    def frame(self, stamps):
        return pd.DataFrame(index=pd.to_datetime(stamps, utc=True))

    def instrument(self, name):
        return dict(id=name, symbol=name,
                    exchange='FX_IDC' if name == 'JPYUSD' else 'TVC')

    def test_native_yield_winter_opens_and_close(self):
        # All four independently fetched streams contain these labels.
        stamps = ['2026-11-02T00:00Z', '2026-11-04T00:00Z',
                  '2026-11-06T22:00Z', '2026-11-09T00:00Z']
        bars = self.frame(stamps)
        for name in self.YIELDS:
            with self.subTest(name=name):
                times = confirmation_times(bars, self.instrument(name), '30m')
                self.assertEqual(times, list(bars.index + pd.Timedelta(minutes=30)))

    def test_provider_metadata_19_hour_open_and_prefix_are_separate(self):
        for name in self.YIELDS:
            inst = self.instrument(name)
            self.assertEqual(policy_for(inst)['session_open_by_tf']['30m'], '19:00')
            bars = self.frame(['2026-10-22T21:00Z', '2026-10-23T00:00Z', '2026-10-23T00:30Z'])
            status = assess_freshness(bars.index, inst, '30m',
                pd.Timestamp('2026-10-23T01:00Z'), bars.index[-1], bars.index[0])
            self.assertEqual(status['status'], 'current')
            self.assertEqual(status['session_open_variance_count'], 2)
            self.assertEqual(status['session_open_variance_bar_opens'],
                ['2026-10-22T23:00:00+00:00', '2026-10-22T23:30:00+00:00'])

    def test_yield_checkpoint_resume_and_removed_interior(self):
        for name in self.YIELDS:
            inst = self.instrument(name)
            bars = self.frame(['2026-11-03T22:00Z', '2026-11-04T00:00Z',
                               '2026-11-04T00:30Z', '2026-11-04T01:00Z'])
            now = pd.Timestamp('2026-11-04T01:30Z')
            clean = assess_freshness(bars.index, inst, '30m', now, bars.index[-1], bars.index[0])
            self.assertEqual(clean['status'], 'current')
            broken = assess_freshness(bars.index.delete(2), inst, '30m', now,
                                     bars.index[-1], bars.index[0])
            self.assertEqual(broken['status'], 'interior_gap')
            self.assertEqual(broken['missing_bar_opens'], ['2026-11-04T00:30:00+00:00'])

    def test_yield_invalid_and_future_bars_not_confirmed(self):
        for name in self.YIELDS:
            inst = self.instrument(name)
            for stamp in ('2026-11-03T23:30Z', '2026-11-04T00:15Z'):
                with self.assertRaises(ValueError):
                    confirmation_times(self.frame([stamp]), inst, '30m')
            bars = self.frame(['2026-11-04T00:00Z'])
            closing = confirmation_times(bars, inst, '30m')[0]
            self.assertGreater(closing, pd.Timestamp('2026-11-04T00:29:59Z'))

    def test_entire_yield_session_missing_is_not_optional_prefix(self):
        inst = self.instrument('US02Y')
        bars = self.frame(['2026-11-03T22:00Z', '2026-11-05T00:00Z'])
        status = assess_freshness(bars.index, inst, '30m', pd.Timestamp('2026-11-05T00:30Z'),
                                  bars.index[-1], bars.index[0])
        self.assertEqual(status['status'], 'interior_gap')
        self.assertIn('2026-11-04T00:00:00+00:00', status['missing_bar_opens'])

    def test_jpy_native_final_bar_and_historical_cutover(self):
        inst = self.instrument('JPYUSD')
        for stamp, close in [('2026-10-16T21:00Z', '2026-10-16T21:30Z'),
                             ('2026-10-23T21:30Z', '2026-10-23T22:00Z'),
                             ('2026-10-30T21:30Z', '2026-10-30T22:00Z'),
                             ('2026-11-06T21:30Z', '2026-11-06T22:00Z')]:
            self.assertEqual(confirmation_times(self.frame([stamp]), inst, '30m'),
                             [pd.Timestamp(close)])
        for stamp in ('2026-10-16T21:30Z', '2026-10-23T22:00Z', '2026-11-07T12:00Z'):
            with self.assertRaises(ValueError):
                confirmation_times(self.frame([stamp]), inst, '30m')

    def test_jpy_expected_grid_recomputes_at_cutover(self):
        inst = self.instrument('JPYUSD')
        expected, _ = _expected(inst, '30m', pd.Timestamp('2026-10-16T20:30Z'),
                                pd.Timestamp('2026-10-23T22:00Z'))
        self.assertNotIn(pd.Timestamp('2026-10-16T21:30Z'), expected)
        self.assertIn(pd.Timestamp('2026-10-23T21:30Z'), expected)

    def test_jpy_absent_new_last_bar_is_stale_not_weekend(self):
        inst = self.instrument('JPYUSD')
        bars = self.frame(['2026-10-23T20:30Z', '2026-10-23T21:00Z'])
        status = assess_freshness(bars.index, inst, '30m', pd.Timestamp('2026-10-24T12:00Z'),
                                  bars.index[-1], bars.index[0])
        self.assertEqual(status['status'], 'stale_tail')
        self.assertEqual(status['missing_bar_opens'], ['2026-10-23T21:30:00+00:00'])
        complete = self.frame(['2026-10-23T20:30Z', '2026-10-23T21:00Z', '2026-10-23T21:30Z'])
        status = assess_freshness(complete.index, inst, '30m', pd.Timestamp('2026-10-24T12:00Z'),
                                  complete.index[-1], complete.index[0])
        self.assertEqual(status['status'], 'current')

    def test_dxy_unproven_reopen_gap_remains_visible(self):
        inst = self.instrument('DXY')
        bars = self.frame(['2026-10-23T20:30Z', '2026-10-25T23:00Z'])
        status = assess_freshness(bars.index, inst, '30m', pd.Timestamp('2026-10-25T23:30Z'),
                                  bars.index[-1], bars.index[0])
        self.assertEqual(status['status'], 'interior_gap')
        self.assertEqual(status['missing_bar_opens'],
            ['2026-10-25T22:00:00+00:00', '2026-10-25T22:30:00+00:00'])


if __name__ == '__main__':
    unittest.main()
