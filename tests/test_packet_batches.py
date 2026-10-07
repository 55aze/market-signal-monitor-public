import copy
import json
import unittest
from unittest.mock import Mock

from market_signal_monitor.notion import Notion, NotionHTTPError
from market_signal_monitor.report_packet import (
    packet, publish_packet, delivery_receipt, PacketBudgetError, PacketSerializationError,
)
from market_signal_monitor.state_engine import seed, acknowledge, identity

NOW = '2026-10-07T04:00:00Z'
SINCE = '2026-10-04T04:00:00Z'


def states_with_backlog(count=63):
    s = seed({'Ticker': 'TEST'})
    s['pending'] = [dict(id=identity('event', i), kind='RAW_SIGNAL', ticker='TEST',
                         source_event_id=f'raw-{i}', origin_tf='30m', signal='Bottom',
                         at=f'2026-09-24T{i // 60:02}:{i % 60:02}:00Z',
                         bar_at='2026-09-23T13:30:00Z', confirmed_at='2026-09-23T14:00:00Z',
                         price=100 + i, value_unit='Price') for i in range(count)]
    return [s]


def read_receipt(value):
    client = Mock()
    client.request.return_value = {'properties': {
        'Packet': {'type': 'rich_text', 'rich_text': []},
        'Delivered IDs': {'type': 'rich_text', 'rich_text': [{'plain_text': json.dumps(value)}]}}}
    return delivery_receipt(client, 'report')


class PacketBatchTests(unittest.TestCase):
    def test_oldest_first_retry_and_ack_drain_every_item_once(self):
        states = states_with_backlog()
        original = copy.deepcopy(states)
        expected = {e['id'] for s in states for e in s['pending']}
        consumed = set()
        while any(s['pending'] for s in states):
            data = packet(states, now=NOW, since=SINCE)
            retry = packet(list(reversed(states)), now=NOW, since=SINCE)
            self.assertEqual(data, retry)
            ids = data['acknowledgement_ids']
            self.assertLessEqual(len(ids), 25)
            self.assertFalse(consumed.intersection(ids))
            self.assertEqual(ids, [i['id'] for i in data['report_items']])
            self.assertEqual(ids, [e['id'] for e in data['raw_signals']])
            self.assertIn('30m Bottom', data['report_items'][0]['line'])
            self.assertIn('bar_at=', data['report_items'][0]['line'])
            client = Mock()
            client.request.side_effect = [RuntimeError('response lost'), {}]
            before = copy.deepcopy(states)
            with self.assertRaises(RuntimeError):
                publish_packet(client, 'report', data)
            publish_packet(client, 'report', data)
            self.assertEqual(client.request.call_args_list[0], client.request.call_args_list[1])
            self.assertEqual(states, before)
            receipt = read_receipt(dict(packet_id=data['packet_id'], item_ids=ids,
                                       delivered_at=None, delivery_evidence='prior_complete_report'))
            states = [acknowledge(s, receipt, confirmed_at=NOW) for s in states]
            self.assertEqual([acknowledge(s, receipt, confirmed_at=NOW) for s in states], states)
            consumed.update(ids)
        self.assertEqual(consumed, expected)
        self.assertEqual(len(original[0]['pending']), 63)
        self.assertIsNone(states[0]['delivered'][0]['delivered_at'])
        self.assertEqual(states[0]['delivered'][0]['delivery_evidence'], 'prior_complete_report')

    def test_character_budget_counts_operational_payload_and_shrinks_prefix(self):
        states = states_with_backlog(8)
        one = packet(states, now=NOW, since=SINCE, max_items=1,
                     operational_errors=[{'kind': 'x' * 200}], coverage='partial')
        limit = len(json.dumps(one, ensure_ascii=False, separators=(',', ':')))
        data = packet(states, now=NOW, since=SINCE, max_chars=limit,
                      operational_errors=[{'kind': 'x' * 200}], coverage='partial')
        self.assertEqual(data['batch']['included'], 1)
        self.assertEqual(data['batch']['remaining'], 7)
        self.assertEqual(len(states[0]['pending']), 8)

    def test_material_context_is_scoped_to_selected_items(self):
        states = states_with_backlog(2)
        other = seed({'Ticker': 'OTHER'})
        other['pending'] = [dict(id='later', ticker='OTHER', kind='OPPORTUNITY', at=NOW)]
        other['structures'] = {'1D': {'extra': 'x' * 180000}}
        data = packet(states + [other], now=NOW, since=SINCE, max_items=2)
        self.assertEqual(data['affected_states'], [])
        self.assertNotIn('later', data['acknowledgement_ids'])
        self.assertEqual(data['batch']['remaining'], 1)

    def test_oversized_single_item_and_empty_packet_fail_explicitly(self):
        states = states_with_backlog(1)
        states[0]['pending'][0]['extra'] = 'x' * 175000
        before = copy.deepcopy(states)
        with self.assertRaises(PacketBudgetError) as caught:
            packet(states, now=NOW, since=SINCE)
        self.assertGreater(caught.exception.diagnostics['chars'], 175000)
        self.assertEqual(states, before)
        with self.assertRaises(PacketBudgetError):
            packet([], now=NOW, since=SINCE, operational_errors=[{'kind': 'x' * 175000}])

    def test_new_opportunity_does_not_starve_old_backlog(self):
        states = states_with_backlog(30)
        first = packet(states, now=NOW, since=SINCE)
        states[0]['pending'].append(dict(id='new', ticker='TEST', kind='OPPORTUNITY', at=NOW))
        second = packet(states, now=NOW, since=SINCE)
        self.assertEqual(first['acknowledgement_ids'], second['acknowledgement_ids'])

    def test_unknown_timestamp_needs_explicit_complete_report_evidence(self):
        value = dict(packet_id=identity('packet', ['a']), item_ids=['a'], delivered_at=None)
        with self.assertRaisesRegex(ValueError, 'evidence'):
            read_receipt(value)
        value['delivery_evidence'] = 'prior_complete_report'
        value['report_ref'] = 'visible prior report'
        self.assertEqual(read_receipt(value).report_ref, value['report_ref'])
        for bad in ('2099-01-01T00:00:00Z', '2026-09-01', 1):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                read_receipt(dict(value, delivered_at=bad))
        del value['delivered_at']
        with self.assertRaises(ValueError):
            read_receipt(value)

    def test_unknown_time_marks_raw_reported_and_preserves_it_on_retry(self):
        client = Notion('dummy', 'events', 'tickers')
        client.query = Mock(return_value=[{'id': 'raw-page', 'properties': {}}])
        client.request = Mock()
        client.mark_reported('raw', None, 'batch')
        props = client.request.call_args.args[2]['properties']
        self.assertEqual(props['Reported At'], {'date': None})
        self.assertEqual(props['Report Status']['select']['name'], 'Reported')
        self.assertTrue(props['Surfaced']['checkbox'])
        client.query.return_value = [{'id': 'raw-page', 'properties': props}]
        client.request.reset_mock()
        client.mark_reported('raw', NOW, 'later-batch')
        client.request.assert_not_called()

    def test_http_error_keeps_safe_status(self):
        response = Mock(ok=False, status_code=400)
        client = Notion('dummy', 'events', 'tickers', session=Mock())
        client.session.request.return_value = response
        with self.assertRaises(NotionHTTPError) as caught:
            client.request('PATCH', 'pages/private', {})
        self.assertEqual(caught.exception.http_status, 400)

    def test_non_finite_data_is_diagnosed_before_any_write(self):
        client = Mock()
        with self.assertRaises(PacketSerializationError):
            publish_packet(client, 'report', {'price': float('nan')})
        client.request.assert_not_called()
