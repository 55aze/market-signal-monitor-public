import unittest
from copy import deepcopy
from market_signal_monitor.state_engine import seed
from market_signal_monitor.report_view import watch_context, compact_structure
from market_signal_monitor.report_packet import packet

NOW = '2026-10-16T00:15:00Z'
INSTRUMENTS = [{'id':'TEST', 'market_timezone':'America/New_York'}]

def state():
    return seed({'Ticker':'TEST'})

def event(tf='1D', signal='Sell', at='2026-10-05T13:30:00Z', confirmed='2026-10-05T20:00:00Z'):
    return dict(event_id=at+tf, timeframe=tf, signal=signal, timestamp=at, confirmation_at=confirmed)

class ReportViewTests(unittest.TestCase):
    def test_highest_tf_anchor_not_newest_lower_tf_and_all_cells(self):
        s=state();s['lineage']=[event(),event('4H',at='2026-10-15T17:30:00Z',confirmed='2026-10-15T20:00:00Z')]
        row=watch_context([s],NOW,INSTRUMENTS)['rows'][0]
        self.assertEqual(row['group_date'],'2026-10-05')
        self.assertEqual(row['highest_signal_tf'],'1D')
        self.assertEqual(list(row['cells']),['1W','1D','4H','30m'])
        self.assertEqual(row['cells']['4H']['signals'][0]['timestamp'],'2026-10-15T17:30:00Z')
        self.assertEqual(row['cells']['4H']['freshness'],'unverified_or_stale')

    def test_cluster_strict_three_distinct_bars_and_48h(self):
        s=state()
        s['lineage']=[event('30m','Bottom',f'2026-10-15T{h}:00:00Z',f'2026-10-15T{h}:30:00Z') for h in ('14','15')]
        s['lineage']+=deepcopy(s['lineage'])
        self.assertEqual(watch_context([s],NOW,INSTRUMENTS)['row_count'],0)
        s['lineage'].append(event('30m','Bottom','2026-10-15T16:00:00Z','2026-10-15T16:30:00Z'))
        self.assertEqual(watch_context([s],NOW,INSTRUMENTS)['rows'][0]['bottom_30m_48h'],3)

    def test_current_structure_independent_of_signal_and_old_highest_label(self):
        s=state();s['highest_sell_tf']='1W'
        self.assertEqual(watch_context([s],NOW,INSTRUMENTS)['row_count'],0)
        s['lineage']=[event()]
        st=dict(timestamp='2026-10-15T13:30:00Z',confirmed_at='2026-10-15T20:00:00Z',snapshot={'values':dict(Close=140,EMA200=80,blueUpperBand=120,blueLowerBand=110,yellowUpperBand=100,yellowLowerBand=90)})
        s['structures']['1D']=st
        s['coverage']={'1D':{'status':'current','expected_latest':st['timestamp']}}
        cell=watch_context([s],NOW,INSTRUMENTS)['rows'][0]['cells']['1D']
        self.assertEqual(cell['structure'],'bull')
        self.assertEqual(cell['freshness'],'current')
        self.assertNotEqual(cell['bar_at'],cell['signals'][0]['timestamp'])
        s['coverage']['1D']['expected_latest']='2026-10-16T13:30:00Z'
        self.assertEqual(watch_context([s],NOW,INSTRUMENTS)['rows'][0]['cells']['1D']['freshness'],'unverified_or_stale')

    def test_internal_records_retained_but_not_delivery_or_receipt(self):
        s=state()
        s['pending']=[dict(id='raw',kind='RAW_SIGNAL',ticker='TEST',origin_tf='30m',signal='Bottom',at='2026-10-15T15:00:00Z'),dict(id='internal',kind='TICKER_STAGE_CHANGE',ticker='TEST',at='2026-10-15T15:00:00Z')]
        before=deepcopy(s)
        p=packet([s],now=NOW,since='2026-10-13T00:15:00Z',signal_report=True,instruments=INSTRUMENTS)
        self.assertEqual(p['acknowledgement_ids'],['raw'])
        self.assertEqual(p['internal_record_count'],1)
        self.assertEqual(s,before)
        self.assertEqual(p['batch']['total_pending'],1)
