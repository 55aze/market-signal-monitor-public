import unittest

from market_signal_monitor.report_packet import packet, MAX_BATCH_ITEMS, MAX_PACKET_CHARS
from market_signal_monitor.state_engine import seed, identity

NOW = '2026-10-11T08:00:00Z'
SINCE = '2026-10-08T08:00:00Z'


def state_with_backlog(count):
    state = seed({'Ticker': 'TEST'})
    state['pending'] = [
        dict(
            id=identity('event', i),
            kind='RAW_SIGNAL',
            ticker='TEST',
            source_event_id=f'raw-{i}',
            origin_tf='30m',
            signal='Bottom',
            at=f'2026-10-09T{i // 60:02}:{i % 60:02}:00Z',
            bar_at='2026-10-09T13:30:00Z',
            confirmed_at='2026-10-09T14:00:00Z',
            price=100 + i,
            value_unit='Price',
        )
        for i in range(count)
    ]
    return state


class BacklogBatchLimitTests(unittest.TestCase):
    def test_ordinary_21_item_backlog_drains_in_one_packet(self):
        data = packet([state_with_backlog(21)], now=NOW, since=SINCE)
        self.assertEqual(MAX_BATCH_ITEMS, 100)
        self.assertEqual(MAX_PACKET_CHARS, 50000)
        self.assertEqual(data['batch']['total_pending'], 21)
        self.assertEqual(data['batch']['included'], 21)
        self.assertEqual(data['batch']['remaining'], 0)
        self.assertEqual(len(data['report_items']), 21)
        self.assertEqual(len(data['acknowledgement_ids']), 21)

    def test_backlog_is_capped_at_100_items_per_packet(self):
        data = packet([state_with_backlog(125)], now=NOW, since=SINCE)
        self.assertEqual(data['batch']['total_pending'], 125)
        self.assertEqual(data['batch']['included'], 100)
        self.assertEqual(data['batch']['remaining'], 25)


if __name__ == '__main__':
    unittest.main()
