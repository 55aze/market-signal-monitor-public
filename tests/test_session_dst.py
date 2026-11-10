"""Wall-clock session anchors must survive both US DST transitions."""
import unittest
import pandas as pd
from market_signal_monitor.confirmation import _wall, confirmation_times
from market_signal_monitor.freshness import assess_freshness, scan_due


class SessionDSTTests(unittest.TestCase):
    # Literal UTC offsets are independent of the production grid generator.
    DAYS = [('2026-03-08', 4), ('2026-11-01', 5),
            ('2026-07-12', 4), ('2026-01-11', 5)]

    def test_wall_clock_on_transition_and_ordinary_days(self):
        for day, offset in self.DAYS:
            for hour in (17, 18, 19, 20):
                with self.subTest(day=day, hour=hour):
                    expected = pd.Timestamp(day, tz='UTC') + pd.Timedelta(hours=hour+offset)
                    self.assertEqual(_wall(day, f'{hour}:00', 'America/New_York'), expected)

    def test_all_session_timeframes_and_continuity(self):
        instruments = [dict(id=x, symbol=x, exchange='TVC')
                       for x in ('US02Y', 'US05Y', 'US10Y', 'US30Y', 'DXY')]
        instruments.append(dict(id='XAUUSD', symbol='XAUUSD', exchange='OANDA'))
        for day, offset in self.DAYS:
            for instrument in instruments:
                treasury = instrument['id'].startswith('US')
                for tf in ('30m', '4H', '1D', '1W'):
                    with self.subTest(day=day, instrument=instrument['id'], tf=tf):
                        hour = 19 if treasury else (18 if tf == '30m' else 17)
                        opening = pd.Timestamp(day, tz='UTC') + pd.Timedelta(hours=hour+offset)
                        delta = {'30m': pd.Timedelta(minutes=30), '4H': pd.Timedelta(hours=4),
                                 '1D': pd.Timedelta(days=1), '1W': pd.Timedelta(days=5)}[tf]
                        # DXY Sunday daily session runs 17:00 to Monday 19:00.
                        if instrument['id'] == 'DXY' and tf == '1D':
                            delta += pd.Timedelta(hours=2)
                        close = opening + delta
                        index = pd.DatetimeIndex([opening])
                        self.assertEqual(confirmation_times(pd.DataFrame(index=index), instrument, tf), [close])
                        checkpoint = opening - pd.Timedelta(minutes=30)
                        if tf != '30m':
                            self.assertFalse(scan_due(instrument, tf, checkpoint, close-pd.Timedelta(seconds=1)))
                        self.assertTrue(scan_due(instrument, tf, checkpoint, close))
                        status = assess_freshness(index, instrument, tf, close, opening, checkpoint)
                        self.assertEqual(status['status'], 'current')
                        self.assertEqual(status['missing_count'], 0)

    def test_missing_interior_and_tail_bars_still_fail(self):
        for name, exchange, hour in [('DXY', 'TVC', 17), ('XAUUSD', 'OANDA', 17), ('US02Y', 'TVC', 19)]:
            instrument = dict(id=name, symbol=name, exchange=exchange)
            for day, offset in self.DAYS:
                opening = pd.Timestamp(day, tz='UTC') + pd.Timedelta(hours=hour+offset)
                index = pd.date_range(opening, periods=3, freq='4h')
                gap = assess_freshness(index[[0, 2]], instrument, '4H', index[-1]+pd.Timedelta(hours=4), index[-1], index[0])
                self.assertEqual(gap['status'], 'interior_gap')
                self.assertEqual(gap['missing_bar_opens'], [index[1].isoformat()])
                tail = assess_freshness(index[:2], instrument, '4H', index[-1]+pd.Timedelta(hours=4), index[1], index[0])
                self.assertEqual(tail['status'], 'stale_tail')

    def test_wrong_treasury_transition_anchor_still_rejected(self):
        instrument = dict(id='US02Y', symbol='US02Y', exchange='TVC')
        for stamp in ('2026-11-01T23:00Z', '2026-03-09T00:00Z'):
            for tf in ('4H', '1D', '1W'):
                with self.assertRaises(ValueError):
                    confirmation_times(pd.DataFrame(index=pd.to_datetime([stamp], utc=True)), instrument, tf)

    def test_audited_november_30m_open_is_midnight_utc(self):
        stamp = pd.Timestamp('2026-11-04T00:00Z')
        frame = pd.DataFrame(index=pd.DatetimeIndex([stamp]))
        for name in ('US02Y', 'US05Y', 'US10Y', 'US30Y'):
            instrument = dict(id=name, symbol=name, exchange='TVC')
            with self.subTest(name=name):
                self.assertEqual(confirmation_times(frame, instrument, '30m'),
                                 [stamp + pd.Timedelta(minutes=30)])
                # Higher TF behavior is deliberately unchanged.
                for tf, hours in [('4H', 4), ('1D', 24)]:
                    self.assertEqual(confirmation_times(frame, instrument, tf),
                                     [stamp + pd.Timedelta(hours=hours)])

    def test_optional_reopen_prefix_does_not_allow_out_of_session_30m_bar(self):
        instrument = dict(id='US02Y', symbol='US02Y', exchange='TVC')
        # The preceding session's final bar and an out-of-policy next label.
        # The prefix exception can waive absent aligned bars, not validate an
        # observed bar outside the session; validation must still reject it.
        index = pd.to_datetime(['2026-11-03T22:00Z', '2026-11-03T23:30Z'], utc=True)
        with self.assertRaisesRegex(ValueError, 'overnight provider session anchor'):
            confirmation_times(pd.DataFrame(index=index), instrument, '30m', strict_since=index[0])
