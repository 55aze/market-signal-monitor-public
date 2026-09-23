import json
import unittest
from unittest.mock import Mock
from test_state_engine import initial, bar
from market_signal_monitor.state_pipeline import process_ticker
from market_signal_monitor.state_engine import advance, acknowledge, identity
from market_signal_monitor.report_packet import packet, delivery_receipt, DeliveryReceipt
from market_signal_monitor.backfill import StatusStore
from market_signal_monitor.notion import Notion


class ReportingConsistencyTests(unittest.TestCase):
    def recovered(self, state):
        b = bar(22, tf='30m', snapshot={}, value_unit='Price')
        return process_ticker(state, {'30m':{'bars':[b]}}, [], '2026-09-23T00:00:00Z')

    def test_recovered_lower_tf_clears_gap_without_weekly_origin_bar(self):
        s = initial(tf='1W')
        s['stream_errors'] = {'30m':True}
        s['uncertainty'] = ['Unavailable stream(s): 30m']
        result = self.recovered(s)
        self.assertEqual(result['uncertainty'], [])
        self.assertEqual(result['last_bar'], s['last_bar'])
        self.assertEqual(result['pending'][-1]['kind'], 'DATA_RECOVERED')
        self.assertEqual(self.recovered(result)['pending'], result['pending'])

    def test_persisted_empty_errors_can_recover_but_not_without_evidence(self):
        s = initial(tf='1W')
        s['uncertainty'] = ['Unavailable stream(s): 30m']
        self.assertEqual(self.recovered(s)['uncertainty'], [])
        self.assertEqual(process_ticker(s, {}, [], '2026-09-23T00:00:00Z')['uncertainty'],
                         s['uncertainty'])

    def test_real_replay_gap_not_cleared_by_other_timeframe(self):
        s = initial(tf='1W')
        s['uncertainty'] = ['Origin checkpoint absent from fetched bars; replay gap unknown']
        self.assertEqual(self.recovered(s)['uncertainty'], s['uncertainty'])

    def test_other_failed_stream_still_blocks_recovery(self):
        s = initial(tf='1W')
        s['stream_errors'] = {'30m':True, '4H':True}
        s['uncertainty'] = ['Unavailable stream(s): 30m, 4H']
        self.assertEqual(self.recovered(s)['uncertainty'], ['Unavailable stream(s): 4H'])

    def test_stage_change_has_trigger_bar_and_distinct_detection_clock(self):
        s = advance(initial(), [bar(2),bar(3),bar(4)], [], '2026-09-10T00:00:00Z')
        e = s['pending'][0]
        self.assertEqual(e['bar_at'], '2026-09-04T00:00:00Z')
        self.assertEqual(e['confirmed_at'], '2026-09-04T23:00:00Z')
        self.assertEqual(e['detected_at'], '2026-09-10T00:00:00Z')

    def test_signal_lineage_receipt_and_stable_batch(self):
        e = dict(event_id='raw',ticker='TEST',timeframe='1W',signal='Sell',
                 timestamp='2026-09-07T00:00:00Z',confirmation_at='2026-09-11T23:00:00Z',
                 first_seen='2026-09-12T00:10:00Z')
        s = advance(initial(), [], [e], '2026-09-13T00:00:00Z')
        self.assertTrue(all(x['source_event_id'] == 'raw' for x in s['pending']))
        data = packet([s], now='2026-09-13T00:00:00Z', since='2026-09-10T00:00:00Z')
        later = packet([s], now='2026-09-14T00:00:00Z', since='2026-09-11T00:00:00Z')
        self.assertEqual(data['packet_id'], later['packet_id'])
        receipt = DeliveryReceipt(data['acknowledgement_ids'], data['packet_id'],
                                  '2026-09-13T06:00:00Z')
        delivered = acknowledge(s, receipt, confirmed_at='2026-09-13T07:00:00Z')
        self.assertFalse(delivered['pending'])
        self.assertEqual(delivered['delivered'][0]['delivered_at'], receipt.delivered_at)
        self.assertEqual(delivered['delivered'][0]['receipt_confirmed_at'], '2026-09-13T07:00:00Z')
        self.assertEqual(acknowledge(delivered, receipt, confirmed_at='2026-09-14T00:00:00Z'), delivered)

    def test_every_live_30m_signal_is_a_specific_durable_packet_item(self):
        s = initial()
        e = dict(event_id='raw-30m', ticker='TEST', timeframe='30m', signal='Sell',
                 timestamp='2026-09-23T14:00:00Z', confirmation_at='2026-09-23T14:30:00Z',
                 first_seen='2026-09-23T14:42:00Z', price=106.65,
                 market_timezone='America/New_York')
        first = process_ticker(s, {}, [e], '2026-09-23T15:00:00Z')
        self.assertEqual([item['kind'] for item in first['pending']], ['RAW_SIGNAL'])
        raw = first['pending'][0]
        self.assertEqual((raw['source_event_id'], raw['bar_at'], raw['price']),
                         ('raw-30m', e['timestamp'], 106.65))
        data = packet([first], now='2026-09-23T15:00:00Z', since='2026-09-22T00:00:00Z')
        self.assertEqual(data['raw_signals'], [raw])
        self.assertEqual(data['acknowledgement_ids'], [raw['id']])
        self.assertEqual(data['affected_states'], [])
        self.assertEqual(process_ticker(first, {}, [e], '2026-09-23T16:00:00Z')['pending'],
                         first['pending'])

    def test_raw_signal_survives_lifecycle_freeze_and_receipt(self):
        s = initial()
        s['stream_errors'] = {'4H': True}
        s['uncertainty'] = ['Unavailable stream(s): 4H']
        e = dict(event_id='raw-frozen', ticker='TEST', timeframe='30m', signal='Bottom',
                 timestamp='2026-09-23T14:00:00Z', confirmation_at='2026-09-23T14:30:00Z',
                 first_seen='2026-09-23T14:42:00Z', price=104.0)
        frozen = process_ticker(s, {}, [e], '2026-09-23T15:00:00Z')
        self.assertEqual([item['kind'] for item in frozen['pending']], ['RAW_SIGNAL'])
        self.assertNotIn('raw-frozen', frozen['seen'])
        delivered = acknowledge(frozen, [frozen['pending'][0]['id']])
        self.assertEqual(process_ticker(delivered, {}, [e], '2026-09-23T16:00:00Z')['pending'], [])

    def test_preexisting_seen_signals_do_not_flood_new_raw_outbox(self):
        s = initial()
        s['seen'] = ['old-raw']
        e = dict(event_id='old-raw', ticker='TEST', timeframe='30m', signal='Sell',
                 timestamp='2026-09-22T14:00:00Z', confirmation_at='2026-09-22T14:30:00Z')
        self.assertEqual(process_ticker(s, {}, [e], '2026-09-23T15:00:00Z')['pending'], [])

    def test_future_confirmation_is_not_a_raw_packet_item(self):
        e = dict(event_id='future-raw', ticker='TEST', timeframe='30m', signal='Sell',
                 timestamp='2026-09-23T14:00:00Z', confirmation_at='2026-09-23T16:00:00Z')
        self.assertEqual(process_ticker(initial(), {}, [e], '2026-09-23T15:00:00Z')['pending'], [])

    def test_receipt_wrong_batch_is_rejected(self):
        client = Mock()
        data = {'packet_id':'wrong','item_ids':['abc'],'delivered_at':'2026-09-01T00:00:00Z'}
        client.request.return_value = {'properties':{
            'Packet':{'type':'rich_text','rich_text':[]},
            'Delivered IDs':{'type':'rich_text','rich_text':[{'plain_text':json.dumps(data)}]}}}
        with self.assertRaisesRegex(ValueError, 'does not match'):
            delivery_receipt(client, 'p')
        data['packet_id'] = identity('packet', ['abc'])
        client.request.return_value['properties']['Delivered IDs']['rich_text'][0]['plain_text'] = json.dumps(data)
        receipt = delivery_receipt(client, 'p')
        self.assertEqual(receipt, ['abc'])
        self.assertEqual(receipt.delivered_at, data['delivered_at'])

    def test_unknown_freshness_is_not_zero_missing_or_current(self):
        store = StatusStore(Mock(), 's')
        store.freshness_fields = {'Expected Through','Freshness Status','Missing Count'}
        p = store.freshness_properties({'status':'unverifiable','missing_count':0})
        self.assertEqual(p['Freshness Status']['select']['name'], 'Unverifiable')
        self.assertIsNone(p['Missing Count']['number'])
        self.assertIsNone(p['Expected Through']['date'])

    def test_reported_fields_are_one_patch_and_retry_preserves_first_time(self):
        client = Notion('dummy', 'events', 'tickers')
        client.query = Mock(return_value=[{'id':'page', 'properties':{}}])
        client.request = Mock()
        client.mark_reported('raw', '2026-09-13T06:00:00Z', 'packet')
        p = client.request.call_args.args[2]['properties']
        self.assertTrue(p['Surfaced']['checkbox'])
        self.assertEqual(p['Report Status']['select']['name'], 'Reported')
        self.assertEqual(p['Reported At']['date']['start'], '2026-09-13T06:00:00Z')
        client.query.return_value = [{'id':'page', 'properties':p}]
        client.request.reset_mock()
        client.mark_reported('raw', '2026-09-14T06:00:00Z', 'new')
        client.request.assert_not_called()

    def test_not_due_stream_populates_new_columns_without_checkpoint_advance(self):
        client = Mock()
        store = StatusStore(client, 's')
        store.freshness_fields = {'Expected Through','Freshness Status','Missing Count'}
        f = {'status':'current','expected_latest':'2026-09-14T13:30:00Z',
             'actual_latest':'2026-09-14T13:30:00Z','missing_count':0}
        store._row = Mock(return_value={'id':'p','properties':{
            'Continuous Through':{'date':{'start':f['actual_latest']}},
            'Current Structure':{'rich_text':[{'plain_text':json.dumps({'freshness':f})}]}}})
        page, checkpoint = store.load_live('T:1W')
        props = client.request.call_args.args[2]['properties']
        self.assertEqual(props['Expected Through']['date']['start'], f['expected_latest'])
        self.assertNotIn('Continuous Through', props)
        self.assertNotIn('Last Attempt', props)
