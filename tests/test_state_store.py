import json
import unittest
from unittest.mock import Mock
from market_signal_monitor.state_store import TickerStore
from market_signal_monitor.state_engine import seed, advance, policy


class StoreTests(unittest.TestCase):
    def test_state_and_outbox_share_one_patch_and_retry_is_identical(self):
        client = Mock()
        store = TickerStore(client, 'source')
        store.rows['T'] = {'id':'page'}
        s = seed({'Ticker':'T'})
        e = {'ticker':'T','signal':'Bottom','timeframe':'1D','timestamp':'2026-09-19T00:00:00Z',
             'confirmation_at':'2026-09-20T00:00:00Z','event_id':'abc'}
        s = advance(s, [], [e], '2026-09-21T00:00:00Z', policy())
        client.request.side_effect = [RuntimeError('ambiguous PATCH'), {}]
        with self.assertRaises(RuntimeError):
            store.save('T', s)
        store.save('T', s)
        self.assertEqual(client.request.call_args_list[0], client.request.call_args_list[1])
        props = client.request.call_args.args[2]['properties']
        saved = json.loads(''.join(x['text']['content'] for x in props['Engine State']['rich_text']))
        self.assertEqual(saved['pending'], s['pending'])
        self.assertEqual(props['Stage']['select']['name'], saved['stage'])

    def test_no_silent_truncation(self):
        store = TickerStore(Mock(), 'source')
        s = seed({'Ticker':'T','Lineage':'x'*180000})
        with self.assertRaisesRegex(ValueError,'archival'):
            store.save('T',s)
        store.client.request.assert_not_called()

    def test_packet_write_does_not_overwrite_delivery_receipt(self):
        from market_signal_monitor.report_packet import publish_packet, delivery_receipt
        client=Mock()
        client.request.return_value={'properties':{
            'Packet':{'type':'rich_text','rich_text':[]},
            'Delivered IDs':{'type':'rich_text','rich_text':[{'plain_text':'["abc"]'}]}}}
        self.assertEqual(delivery_receipt(client,'page'),['abc'])
        publish_packet(client,'page',{'should_report':False})
        self.assertEqual(set(client.request.call_args.args[2]['properties']),{'Packet'})

    def test_delivery_receipt_accepts_safe_legacy_csv_and_deduplicates(self):
        from market_signal_monitor.report_packet import delivery_receipt
        first, second = '21cf0ab199642eb1afb37e03', '0e0d122f5e8b8359dd15621b'
        client = Mock()
        client.request.return_value = {'properties': {
            'Packet': {'type':'rich_text','rich_text':[]},
            'Delivered IDs': {'type':'rich_text','rich_text':[
                {'plain_text': f'{first}, {second}, {first}'}]}}}
        self.assertEqual(delivery_receipt(client, 'page'), [first, second])

    def test_delivery_receipt_rejects_arbitrary_malformed_text(self):
        from market_signal_monitor.report_packet import delivery_receipt
        client = Mock()
        client.request.return_value = {'properties': {
            'Packet': {'type':'rich_text','rich_text':[]},
            'Delivered IDs': {'type':'rich_text','rich_text':[
                {'plain_text':'not valid receipt text'}]}}}
        with self.assertRaisesRegex(ValueError, 'JSON string array'):
            delivery_receipt(client, 'page')
