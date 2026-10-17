import unittest
import pandas as pd
from market_signal_monitor.confirmation import confirmation_times, policy_for, market_timezone
from market_signal_monitor.freshness import assess_freshness, _expected
from market_signal_monitor.state_pipeline import process_ticker
from market_signal_monitor.state_engine import seed
from market_signal_monitor.report_view import watch_context


class StreamRecoveryTests(unittest.TestCase):
    def test_bond_holiday_does_not_block_resume_but_real_hole_does(self):
        for name in ['US02Y', 'US05Y', 'US10Y', 'US30Y']:
            i = dict(id=name, symbol=name, exchange='TVC')
            for tf, checkpoint in [('30m','2026-10-09T21:00Z'),
                                   ('4H','2026-10-09T19:00Z'),
                                   ('1D','2026-10-08T23:00Z')]:
                now = pd.Timestamp('2026-10-16T11:15Z')
                cp = pd.Timestamp(checkpoint)
                expected, _ = _expected(i, tf, cp, now)
                # Columbus Day session is closed; next session opens Monday evening.
                self.assertFalse(any(pd.Timestamp('2026-10-11T23:00Z') <= x <
                                     pd.Timestamp('2026-10-12T23:00Z') for x in expected))
                actual = expected[-1]
                result = assess_freshness([cp]+expected, i, tf, now, actual, cp)
                self.assertEqual(result['status'], 'current')
                hole = expected[len(expected)//2]
                result = assess_freshness([cp]+[x for x in expected if x != hole],
                                         i, tf, now, actual, cp)
                self.assertEqual(result['status'], 'interior_gap')
                self.assertIn(hole.isoformat(),result['missing_bar_opens'])

    def test_dxy_and_gold_friday_close_and_weekend_not_next_bar(self):
        for name, exchange in [('DXY','TVC'), ('XAUUSD','OANDA')]:
            i = dict(id=name,symbol=name,exchange=exchange)
            cases = [('30m','2026-10-09T20:30Z'),
                     ('4H','2026-10-09T19:00Z' if name=='DXY' else '2026-10-09T17:00Z'),
                     ('1D','2026-10-08T23:00Z' if name=='DXY' else '2026-10-08T21:00Z'),
                     ('1W','2026-10-04T21:00Z')]
            for tf, stamp in cases:
                b = pd.DataFrame(index=pd.to_datetime([stamp], utc=True))
                self.assertEqual(confirmation_times(b,i,tf)[0],pd.Timestamp('2026-10-09T21:00Z'))
                status=assess_freshness(b.index,i,tf,pd.Timestamp('2026-10-10T12:00Z'),b.index[0],b.index[0])
                self.assertEqual(status['status'],'current')
            self.assertEqual(market_timezone(i),'America/New_York')

    def test_dxy_monday_anchor_and_daily_maintenance(self):
        i = dict(id='DXY',symbol='DXY',exchange='TVC')
        b = pd.DataFrame(index=pd.to_datetime(['2026-10-11T21:00Z'],utc=True))
        self.assertEqual(confirmation_times(b,i,'1D')[0],pd.Timestamp('2026-10-12T23:00Z'))
        for name,exchange,cp,now in [('DXY','TVC','2026-10-15T22:00Z','2026-10-15T23:29Z'),
                                   ('XAUUSD','OANDA','2026-10-15T20:30Z','2026-10-15T21:59Z')]:
            instrument=dict(id=name,symbol=name,exchange=exchange)
            expected,_=_expected(instrument,'30m',pd.Timestamp(cp),pd.Timestamp(now))
            self.assertEqual(expected,[])

    def test_dst_uses_market_wall_time(self):
        i=dict(id='XAUUSD',symbol='XAUUSD',exchange='OANDA')
        b=pd.DataFrame(index=pd.to_datetime(['2026-11-06T21:30Z'],utc=True))
        self.assertEqual(confirmation_times(b,i,'30m')[0],pd.Timestamp('2026-11-06T22:00Z'))

    def test_structure_updates_even_if_lifecycle_frozen(self):
        for uncertainty in [[], ['Late dominant signal requires explicit chronological replay']]:
            s=seed({'Ticker':'BTC'});s['uncertainty']=uncertainty
            observation=dict(timestamp='2026-10-16T04:00:00Z',confirmed_at='2026-10-16T08:00:00Z',
                             snapshot={'values':{}},value_unit='Price')
            batches={'4H':dict(bars=[observation],coverage={'status':'current','expected_latest':observation['timestamp']}),
                     '1D':dict(error=True)}
            result=process_ticker(s,batches,[],'2026-10-16T11:00:00Z')
            self.assertEqual(result['structures']['4H']['timestamp'],observation['timestamp'])
            self.assertEqual(result['stage'],s['stage'])
            self.assertNotIn('1D',result['structures'])

    def test_packet_exposes_exact_stream_identity(self):
        instruments=[dict(id='BTCUSD',notion_ticker='BTC',symbol='BTCUSD',market_timezone='UTC'),
                     dict(id='0100',symbol='100',market_timezone='Asia/Hong_Kong')]
        states=[]
        for ticker in ['BTC','0100']:
            s=seed({'Ticker':ticker})
            s['lineage']=[dict(timeframe='4H',signal='Bottom',timestamp='2026-10-15T04:00Z',confirmation_at='2026-10-15T08:00Z')]
            states.append(s)
        rows=watch_context(states,'2026-10-16T11:00:00Z',instruments)['rows']
        by={r['ticker']:r for r in rows}
        self.assertEqual(by['BTC']['cells']['4H']['stream'],'BTCUSD:4H')
        self.assertEqual(by['0100']['cells']['4H']['stream'],'0100:4H')
        self.assertEqual(by['0100']['provider_symbol'],'100')
