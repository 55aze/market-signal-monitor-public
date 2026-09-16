"""Regression evidence for provider-specific close policies."""
import json
from pathlib import Path
import unittest

import pandas as pd

from market_signal_monitor.confirmation import confirmation_times, policy_for
from market_signal_monitor.freshness import assess_freshness


class PolicyCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = json.loads((Path(__file__).parents[1] / 'config/universe.example.json').read_text())
        cls.instruments = {i['id']: i for i in config['instruments']}

    def test_equities_etfs_and_verified_indices_confirm_friday_without_monday_successor(self):
        names = ('SPY IGV SOXX AAPL GOOGL MSFT NVDA META TSLA AVGO INTC LITE '
                 'SPCX NOW CRM SNOW ORCL OKLO BE MCD NKE MU MRVL NBIS BABA MSTR '
                 'SPX SOX RUT').split()
        cases = {'30m': '2026-09-11T19:30Z', '4H': '2026-09-11T17:30Z',
                 '1D': '2026-09-11T13:30Z', '1W': '2026-09-08T13:30Z'}
        for name in names:
            for tf, stamp in cases.items():
                with self.subTest(name=name, tf=tf):
                    if tf == '30m' and name in {'SPX', 'SOX', 'RUT'}:
                        stamp = '2026-09-11T20:00Z'
                    index = pd.to_datetime([stamp], utc=True)
                    closing = confirmation_times(pd.DataFrame(index=index), self.instruments[name], tf)
                    self.assertEqual(closing[0], pd.Timestamp('2026-09-11T20:00Z'))
                    result = assess_freshness(index, self.instruments[name], tf,
                                             pd.Timestamp('2026-09-13T08:00Z'), index[0], index[0])
                    self.assertEqual(result['status'], 'current')
                    self.assertEqual(result['verification'], 'exchange_calendar')

    def test_new_york_holiday_early_close_and_dst_are_shared(self):
        instrument = self.instruments['AAPL']
        for stamp, expected in [('2025-11-28T14:30Z', '2025-11-28T18:00Z'),
                                ('2026-01-05T14:30Z', '2026-01-05T21:00Z')]:
            index = pd.to_datetime([stamp], utc=True)
            self.assertEqual(confirmation_times(pd.DataFrame(index=index), instrument, '1D')[0],
                             pd.Timestamp(expected))

    def test_verified_indices_accept_provider_30m_close_label(self):
        for name in ['SPX', 'SOX', 'RUT']:
            instrument = self.instruments[name]
            close_label = pd.to_datetime(['2026-09-11T20:00Z'], utc=True)
            self.assertEqual(
                confirmation_times(pd.DataFrame(index=close_label), instrument, '30m')[0],
                pd.Timestamp('2026-09-11T20:00Z'))
            missing_close = assess_freshness(
                pd.to_datetime(['2026-09-11T19:30Z'], utc=True), instrument, '30m',
                pd.Timestamp('2026-09-11T20:01Z'), pd.Timestamp('2026-09-11T19:30Z'),
                pd.Timestamp('2026-09-11T19:30Z'))
            self.assertEqual(missing_close['status'], 'stale_tail')
            self.assertEqual(missing_close['missing_bar_opens'],
                             ['2026-09-11T20:00:00+00:00'])

    def test_shenzhen_reuses_full_trading_day_4h_and_hk_restarts_after_lunch(self):
        for name, stamp, closing in [('399411', '2026-09-11T01:30Z', '2026-09-11T07:00Z'),
                                     ('HSTECH', '2026-09-11T05:00Z', '2026-09-11T08:00Z')]:
            index = pd.to_datetime([stamp], utc=True)
            self.assertEqual(confirmation_times(pd.DataFrame(index=index), self.instruments[name], '4H')[0],
                             pd.Timestamp(closing))
            status = assess_freshness(index, self.instruments[name], '4H',
                                      pd.Timestamp('2026-09-13T08:00Z'), index[0], index[0])
            self.assertEqual(status['status'], 'current')

    def test_treasury_yields_share_provider_session_and_holiday_calendar(self):
        for name in ['US02Y', 'US05Y', 'US10Y', 'US30Y']:
            instrument = self.instruments[name]
            self.assertNotIn('confirmation', instrument)
            self.assertEqual(policy_for(instrument)['id'], 'tvc-us-yield-session-v2')
            cases = {
                '30m': ('2026-09-11T21:00Z', '2026-09-11T21:30Z'),
                '4H': ('2026-09-11T19:00Z', '2026-09-11T23:00Z'),
                '1D': ('2026-09-10T23:00Z', '2026-09-11T23:00Z'),
                '1W': ('2026-09-07T23:00Z', '2026-09-11T23:00Z'),
            }
            for tf, (stamp, closing) in cases.items():
                with self.subTest(name=name, tf=tf):
                    index = pd.to_datetime([stamp], utc=True)
                    result = confirmation_times(pd.DataFrame(index=index), instrument, tf)
                    self.assertEqual(result[0], pd.Timestamp(closing))
            # Labor Day itself is not treated as a trading date; Tuesday's daily
            # session begins Monday evening at the audited 19:00 ET anchor.
            valid = pd.DataFrame(index=pd.to_datetime(['2026-09-07T23:00Z'], utc=True))
            self.assertEqual(confirmation_times(valid, instrument, '1D')[0],
                             pd.Timestamp('2026-09-08T23:00Z'))

    def test_jpyusd_fixed_24x5_grid(self):
        instrument = self.instruments['JPYUSD']
        self.assertEqual(policy_for(instrument)['id'], 'fx-idc-24x5-provider-grid-v2')
        cases = {
            '30m': ('2026-09-11T21:00Z', '2026-09-11T21:30Z'),
            '4H': ('2026-09-11T18:00Z', '2026-09-11T22:00Z'),
            '1D': ('2026-09-10T22:00Z', '2026-09-11T22:00Z'),
            '1W': ('2026-09-06T22:00Z', '2026-09-11T22:00Z'),
        }
        for tf, (stamp, closing) in cases.items():
            index = pd.to_datetime([stamp], utc=True)
            self.assertEqual(confirmation_times(pd.DataFrame(index=index), instrument, tf)[0],
                             pd.Timestamp(closing))
        friday = pd.to_datetime(['2026-09-11T20:30Z', '2026-09-11T21:00Z'], utc=True)
        status = assess_freshness(friday, instrument, '30m',
                                  pd.Timestamp('2026-09-11T22:00Z'), friday[-1], friday[0])
        self.assertEqual(status['status'], 'current')
        self.assertEqual(status['missing_bar_opens'], [])

    def test_remaining_special_feeds_stay_unclassified(self):
        for name in ['VIX', 'DXY', 'XAUUSD']:
            self.assertIsNone(policy_for(self.instruments[name]), name)
        index = pd.to_datetime(['2026-09-11T00:00Z'], utc=True)
        status = assess_freshness(index, self.instruments['BRENT'], '4H',
                                  pd.Timestamp('2026-09-13T08:00Z'), index[0], index[0])
        self.assertEqual(status['verification'], 'unsupported_continuous')

    def test_bad_equity_anchor_is_rejected_not_silently_confirmed(self):
        index = pd.to_datetime(['2026-09-11T20:00Z'], utc=True)
        with self.assertRaises(ValueError):
            confirmation_times(pd.DataFrame(index=index), self.instruments['AAPL'], '30m')


if __name__ == '__main__':
    unittest.main()
