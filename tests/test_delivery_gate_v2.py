import json
import unittest
from unittest.mock import Mock

from market_signal_monitor.report_packet import delivery_receipt
from market_signal_monitor.state_engine import acknowledge, identity, seed

NOW = '2026-10-19T15:00:00Z'


def read_receipt(ids):
    value = {
        'packet_id': identity('packet', sorted(set(ids))),
        'item_ids': ids,
        'delivered_at': None,
        'delivery_evidence': 'prior_complete_report',
    }
    client = Mock()
    client.request.return_value = {'properties': {
        'Packet': {'type': 'rich_text', 'rich_text': []},
        'Delivered IDs': {
            'type': 'rich_text',
            'rich_text': [{'plain_text': json.dumps(value)}],
        },
    }}
    return delivery_receipt(client, 'report')


class DeliveryGateV2Tests(unittest.TestCase):
    def test_partial_overlap_can_deliver_uncovered_subset_and_preserve_union(self):
        old_id = identity('event', 'old')
        new_ids = [identity('event', 'new-1'), identity('event', 'new-2')]

        state = seed({'Ticker': 'TEST'})
        state['pending'] = [
            {'id': old_id, 'kind': 'RAW_SIGNAL', 'ticker': 'TEST'},
            *[{'id': event_id, 'kind': 'RAW_SIGNAL', 'ticker': 'TEST'}
              for event_id in new_ids],
        ]

        persisted = read_receipt([old_id])
        covered = set(persisted)
        uncovered = [item['id'] for item in state['pending'] if item['id'] not in covered]
        self.assertEqual(uncovered, new_ids)

        # The backend receipt contract accepts a receipt for exactly the displayed
        # uncovered subset; it need not reuse the mixed parent Packet identity.
        subset_receipt = read_receipt(uncovered)
        self.assertEqual(
            subset_receipt.packet_id,
            identity('packet', sorted(set(uncovered))),
        )

        # On the following run, preserve any still-unconsumed older evidence by
        # acknowledging the union. The scanner can then drain the entire outbox.
        union_ids = list(dict.fromkeys([*persisted, *subset_receipt]))
        union_receipt = read_receipt(union_ids)
        drained = acknowledge(state, union_receipt, confirmed_at=NOW)
        self.assertEqual(drained['pending'], [])
        self.assertEqual(
            union_receipt.packet_id,
            identity('packet', sorted(set(union_ids))),
        )


if __name__ == '__main__':
    unittest.main()
