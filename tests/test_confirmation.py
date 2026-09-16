import unittest
import pandas as pd
from market_signal_monitor.confirmation import confirmation_times, policy_id
from market_signal_monitor.events import extract_events

QQQ = {'id':'QQQ','exchange':'NASDAQ','symbol':'QQQ'}
def bars(stamps):
    return pd.DataFrame({'close':100.,'high':101.,'low':99.,'open':100.},index=pd.to_datetime(stamps,utc=True))

class ConfirmationTests(unittest.TestCase):
    def test_weekend_final_bars(self):
        cases=[('30m','2026-09-04T19:30Z','2026-09-04T20:00Z'),
               ('4H','2026-09-04T17:30Z','2026-09-04T20:00Z'),
               ('1D','2026-09-04T13:30Z','2026-09-04T20:00Z'),
               ('1W','2026-08-31T13:30Z','2026-09-04T20:00Z')]
        for tf,stamp,close in cases:
            b=bars([stamp]); c=b.copy(); c['DXDX']=1;c['DBJGXC']=0
            confirmations=confirmation_times(b,QQQ,tf)
            self.assertEqual(confirmations[0],pd.Timestamp(close))
            before=extract_events(b,c,QQQ,tf,'v1',pd.Timestamp(close)-pd.Timedelta(seconds=1),backfill=True,warmup=0,confirmations=confirmations)
            after=extract_events(b,c,QQQ,tf,'v1',pd.Timestamp(close),backfill=True,warmup=0,confirmations=confirmations)
            self.assertEqual(before,[]);self.assertEqual(len(after),1)
            self.assertIsNone(after[0]['next_bar_at'])
            self.assertEqual(after[0]['close_status'],'calendar_close')

    def test_early_close_and_dst(self):
        b=bars(['2025-11-28T14:30Z'])
        self.assertEqual(confirmation_times(b,QQQ,'4H')[0],pd.Timestamp('2025-11-28T18:00Z'))
        b=bars(['2026-01-05T14:30Z','2026-07-06T13:30Z'])
        self.assertEqual(confirmation_times(b,QQQ,'1D'),[pd.Timestamp('2026-01-05T21:00Z'),pd.Timestamp('2026-07-06T20:00Z')])

    def test_friday_holiday_and_monday_holiday(self):
        self.assertEqual(confirmation_times(bars(['2026-03-30T13:30Z']),QQQ,'1W')[0],pd.Timestamp('2026-04-02T20:00Z'))
        self.assertEqual(confirmation_times(bars(['2026-09-08T13:30Z']),QQQ,'1W')[0],pd.Timestamp('2026-09-11T20:00Z'))

    def test_bad_anchor_rejected(self):
        with self.assertRaises(ValueError):
            confirmation_times(bars(['2026-09-04T14:00Z']),QQQ,'4H')

    def test_historical_tv_anchor_anomalies_before_window_are_ignored(self):
        b=bars(['2018-11-23T18:30Z','2026-09-04T17:30Z'])
        result=confirmation_times(b,QQQ,'4H',strict_since=pd.Timestamp('2023-09-01T00:00Z'))
        self.assertTrue(pd.isna(result[0]))
        self.assertEqual(result[1],pd.Timestamp('2026-09-04T20:00Z'))
        self.assertEqual(
            confirmation_times(bars(['2006-12-27T14:30Z']),QQQ,'1D')[0],
            pd.Timestamp('2006-12-27T21:00Z'))

    def test_holiday_monday_week_label_maps_to_friday_close(self):
        result=confirmation_times(bars(['1999-05-31T13:30Z']),QQQ,'1W')
        self.assertEqual(result[0],pd.Timestamp('1999-06-04T20:00Z'))

    def test_identity_unchanged(self):
        b=bars(['2026-09-03T13:30Z','2026-09-04T13:30Z']);c=b.copy();c['DXDX']=1;c['DBJGXC']=0
        kwargs=dict(backfill=True,warmup=0)
        old=extract_events(b,c,QQQ,'1D','v1',pd.Timestamp('2026-09-06T00:00Z'),**kwargs)
        new=extract_events(b,c,QQQ,'1D','v1',pd.Timestamp('2026-09-06T00:00Z'),confirmations=confirmation_times(b,QQQ,'1D'),**kwargs)
        self.assertEqual(old[0]['event_id'],new[0]['event_id'])

    def test_continuous_markets_confirm_by_elapsed_interval(self):
        for instrument in [
            {'id':'BTCUSD','exchange':'BITSTAMP','symbol':'BTCUSD',
             'confirmation':{'type':'continuous_interval','id':'btc-v1'}},
            {'id':'BRENT','exchange':'ICEEUR','symbol':'BRN1!',
             'confirmation':{'type':'continuous_interval','id':'brent-candidate-v1'}}]:
            b=bars(['2026-09-07T00:00Z'])
            self.assertEqual(confirmation_times(b,instrument,'30m')[0],pd.Timestamp('2026-09-07T00:30Z'))
            self.assertEqual(confirmation_times(b,instrument,'4H')[0],pd.Timestamp('2026-09-07T04:00Z'))
            self.assertEqual(confirmation_times(b,instrument,'1D')[0],pd.Timestamp('2026-09-08T00:00Z'))
            self.assertEqual(confirmation_times(b,instrument,'1W')[0],pd.Timestamp('2026-09-14T00:00Z'))

    def test_hkex_close_break_and_holiday_week(self):
        hk={'id':'700','exchange':'HKEX','symbol':'700','market_timezone':'Asia/Hong_Kong',
            'confirmation':{'type':'exchange_calendar','calendar':'HKEX',
                            'timezone':'Asia/Hong_Kong','id':'hk-v1'}}
        self.assertEqual(confirmation_times(bars(['2026-09-07T01:30Z']),hk,'1D')[0],
                         pd.Timestamp('2026-09-07T08:00Z'))
        self.assertEqual(confirmation_times(bars(['2026-09-07T05:00Z']),hk,'30m')[0],
                         pd.Timestamp('2026-09-07T05:30Z'))
        self.assertEqual(confirmation_times(bars(['2023-09-11T05:00Z']),hk,'4H')[0],
                         pd.Timestamp('2023-09-11T08:00Z'))
        self.assertEqual(confirmation_times(bars(['2023-09-11T01:30Z']),hk,'4H')[0],
                         pd.Timestamp('2023-09-11T04:00Z'))
        with self.assertRaises(ValueError):
            confirmation_times(bars(['2026-09-07T04:00Z']),hk,'30m')
        # TradingView has used a later session-open label for some historical
        # HKEX weekly bars (for example the Easter week in 2021).
        self.assertEqual(confirmation_times(bars(['2021-04-07T01:30Z']),hk,'1W')[0],
                         pd.Timestamp('2021-04-09T08:00Z'))
        self.assertEqual(policy_id(hk),'hk-v1')

    def test_sse_regular_session_confirms_without_successor_bar(self):
        sse={'id':'588000','exchange':'SSE','symbol':'588000','extended_session':False}
        self.assertEqual(policy_id(sse),'sse-rth-calendar-v2')
        self.assertEqual(confirmation_times(bars(['2026-09-10T01:30Z']),sse,'1D')[0],
                         pd.Timestamp('2026-09-10T07:00Z'))
        self.assertEqual(confirmation_times(bars(['2026-09-10T05:00Z']),sse,'30m')[0],
                         pd.Timestamp('2026-09-10T05:30Z'))
        # tvDatafeed returns one SSE 4H regular-session bar per day at 09:30;
        # lunch is not a bar close, so confirmation is the 15:00 session close.
        self.assertEqual(confirmation_times(bars(['2026-09-10T01:30Z']),sse,'4H')[0],
                         pd.Timestamp('2026-09-10T07:00Z'))
        with self.assertRaises(ValueError):
            confirmation_times(bars(['2026-09-10T05:00Z']),sse,'4H')

    def test_backfill_refresh_keeps_window_and_existing_payload(self):
        from unittest.mock import Mock, patch
        from test_backfill import Store
        from market_signal_monitor.backfill import run_backfill
        b=bars(['2026-09-02T13:30Z','2026-09-03T13:30Z','2026-09-04T13:30Z'])
        c=b.copy();c['DXDX']=[0,1,1];c['DBJGXC']=0
        config=dict(pine_sha256='abc',warmup=1,timeframes=['1D'],instruments=[QQQ],backfill_months={'1D':36},backfill_limits={'1D':5})
        store=Store();client=Mock();now=pd.Timestamp('2026-09-06T13:00Z')
        with patch('market_signal_monitor.backfill.has_close_policy',return_value=False),patch(
                'market_signal_monitor.backfill.policy_id',return_value='next-bar'),patch(
                'market_signal_monitor.backfill.calculate',return_value=c):
            run_backfill(config,client=client,fetcher=lambda *a:b,selected=['QQQ'],now=now,store=store)
        old=store.state['manifest'][0].copy();window=store.state['end']
        with patch('market_signal_monitor.backfill.calculate',return_value=c), patch('market_signal_monitor.backfill.utcnow',return_value=pd.Timestamp('2026-09-08T00:00Z')):
            report=run_backfill(config,client=client,fetcher=lambda *a:b,selected=['QQQ'],now=now+pd.Timedelta(days=1),store=store)
        self.assertFalse(report['errors'])
        self.assertEqual(store.state['end'],window)
        self.assertEqual(store.state['manifest'][0],old)
        self.assertEqual(len(store.state['manifest']),2)
        self.assertEqual(store.state['coverage_end'],b.index[-1].isoformat())
