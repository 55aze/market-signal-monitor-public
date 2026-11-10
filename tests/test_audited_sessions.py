"""Native session boundaries and fail-closed regression tests."""
import unittest
import pandas as pd
from market_signal_monitor.confirmation import policy_for, confirmation_times
from market_signal_monitor.freshness import assess_freshness, scan_due
from market_signal_monitor.audited_sessions import native_grid

VIX = {'id':'VIX','exchange':'CBOE','symbol':'VIX'}
BRENT = {'id':'BRENT','exchange':'ICEEUR','symbol':'BRN1!',
         'confirmation':{'type':'continuous_interval','id':'brent-nominal-interval-candidate-v1'}}


def confirm(inst, tf, stamps):
    ix=pd.to_datetime(stamps,utc=True)
    return confirmation_times(pd.DataFrame(index=ix),inst,tf,strict_since=ix.min())


def stamps(inst, tf, start, end):
    return pd.DatetimeIndex([op for op, cl in native_grid(policy_for(inst),tf,start,end)
                           if pd.Timestamp(start,tz='UTC') <= op < pd.Timestamp(end,tz='UTC')])


class AuditedSessionTests(unittest.TestCase):
    def test_identity_does_not_match_other_feed(self):
        self.assertIsNone(policy_for({'id':'VIX','exchange':'TVC','symbol':'VIX'}))
        self.assertEqual(policy_for(BRENT)['type'],'audited_native')

    def test_vix_split_session_short_bar(self):
        values=confirm(VIX,'30m',['2026-11-09T14:15Z','2026-11-09T14:30Z','2026-11-09T21:00Z'])
        self.assertEqual(values,list(pd.to_datetime(['2026-11-09T14:26Z','2026-11-09T15:00Z','2026-11-09T21:16Z'],utc=True)))

    def test_vix_intraday_4h_short_segments(self):
        values=confirm(VIX,'4H',['2026-11-09T12:15Z','2026-11-09T18:30Z'])
        self.assertEqual(values,list(pd.to_datetime(['2026-11-09T14:26Z','2026-11-09T21:16Z'],utc=True)))

    def test_vix_daily_does_not_wait_until_next_day(self):
        self.assertEqual(confirm(VIX,'1D',['2026-11-09T08:15Z'])[0],pd.Timestamp('2026-11-09T21:16Z'))

    def test_weekly_current_week_never_confirms_from_partial_query(self):
        self.assertEqual(confirm(VIX,'1W',['2026-11-09T08:15Z'])[0],pd.Timestamp('2026-11-13T21:16Z'))
        self.assertEqual(confirm(BRENT,'1W',['2026-11-08T23:00Z'])[0],pd.Timestamp('2026-11-13T23:00Z'))

    def test_vix_holiday_week_anchor_is_first_actual_day(self):
        self.assertEqual(confirm(VIX,'1W',['2026-09-08T07:15Z'])[0],pd.Timestamp('2026-09-11T20:16Z'))
        with self.assertRaises(ValueError):
            confirm(VIX,'1D',['2026-09-07T07:15Z'])

    def test_vix_early_close(self):
        self.assertEqual(confirm(VIX,'1D',['2026-11-27T08:15Z'])[0],pd.Timestamp('2026-11-27T18:16Z'))

    def test_vix_dst_is_local_wall_time(self):
        values=confirm(VIX,'1D',['2026-10-30T07:15Z','2026-11-02T08:15Z'])
        self.assertEqual(values,list(pd.to_datetime(['2026-10-30T20:16Z','2026-11-02T21:16Z'],utc=True)))

    def test_brent_sunday_reopen_and_short_final_4h(self):
        self.assertEqual(confirm(BRENT,'1D',['2026-11-08T23:00Z'])[0],pd.Timestamp('2026-11-09T23:00Z'))
        self.assertEqual(confirm(BRENT,'4H',['2026-11-10T21:00Z'])[0],pd.Timestamp('2026-11-10T23:00Z'))
        self.assertEqual(confirm(BRENT,'1D',['2026-11-10T01:00Z'])[0],pd.Timestamp('2026-11-10T23:00Z'))

    def test_brent_us_uk_dst_divergence(self):
        values=confirm(BRENT,'1D',['2026-10-25T22:00Z','2026-11-01T23:00Z'])
        self.assertEqual(values,list(pd.to_datetime(['2026-10-26T22:00Z','2026-11-02T23:00Z'],utc=True)))

    def test_brent_explicit_early_close_and_full_holiday(self):
        self.assertEqual(confirm(BRENT,'1D',['2026-11-26T00:00Z'])[0],pd.Timestamp('2026-11-26T18:30Z'))
        with self.assertRaises(ValueError):
            confirm(BRENT,'1D',['2026-12-25T01:00Z'])

    def test_missing_interior_bar_and_entire_day_remain_gaps(self):
        for inst in [VIX,BRENT]:
            ix=stamps(inst,'30m','2026-11-05','2026-11-07')
            now=pd.Timestamp('2026-11-07T02:00Z')
            good=assess_freshness(ix,inst,'30m',now,ix[-1],previous_through=ix[0])
            self.assertEqual(good['status'],'current')
            hole=ix.delete(len(ix)//2)
            bad=assess_freshness(hole,inst,'30m',now,hole[-1],previous_through=hole[0])
            self.assertEqual(bad['status'],'interior_gap')
            self.assertEqual(bad['missing_count'],1)
            missing_day=ix[ix<pd.Timestamp('2026-11-06T00:00Z')]
            tail=assess_freshness(missing_day,inst,'30m',now,missing_day[-1],previous_through=missing_day[0])
            self.assertEqual(tail['status'],'stale_tail')
            self.assertGreater(tail['missing_count'],20)

    def test_open_bar_not_due_before_its_actual_close(self):
        for inst,stamp,close in [(VIX,'2026-11-09T08:15Z','2026-11-09T21:16Z'),
                                 (BRENT,'2026-11-09T23:00Z','2026-11-10T23:00Z')]:
            if inst is BRENT:
                # Tuesday native open is 01:00, not Monday's Sunday open.
                stamp='2026-11-10T01:00Z'
            end=confirm(inst,'1D',[stamp])[0]
            self.assertEqual(end,pd.Timestamp(close))
            self.assertGreater(end,pd.Timestamp(stamp)+pd.Timedelta(minutes=5))

    def test_audit_expiry_is_not_current(self):
        ix=pd.to_datetime(['2026-12-30T08:15Z'],utc=True)
        result=assess_freshness(ix,VIX,'1D',pd.Timestamp('2027-01-04T22:00Z'),ix[-1],previous_through=ix[-1])
        self.assertEqual(result['status'],'unverifiable')
        self.assertIn('out_of_scope',result['verification'])
        with self.assertRaises(ValueError):
            confirm(VIX,'1D',['2027-01-04T08:15Z'])

    def test_unknown_latest_anchor_not_silently_relabelled(self):
        with self.assertRaises(ValueError):
            confirm(VIX,'30m',['2026-11-09T14:00Z'])
        with self.assertRaises(ValueError):
            confirm(BRENT,'30m',['2026-11-10T00:30Z'])

    def test_dxy_gap_is_not_waived_by_other_policies(self):
        inst={'id':'DXY','exchange':'TVC','symbol':'DXY'}
        ix=pd.to_datetime(['2026-10-23T20:30Z','2026-10-25T23:00Z'],utc=True)
        result=assess_freshness(ix,inst,'30m',pd.Timestamp('2026-10-25T23:30Z'),ix[-1],previous_through=ix[0])
        self.assertEqual(result['status'],'interior_gap')
        self.assertIn('2026-10-25T22:00:00+00:00',result['missing_bar_opens'])


if __name__=='__main__':
    unittest.main()
