import unittest
from market_signal_monitor.confirmation import market_timezone
from market_signal_monitor.notion import market_time, event_properties
from test_delivery import events


class MarketTimeTests(unittest.TestCase):
    def test_qqq_dst_and_identity(self):
        event = events(backfill=True)[0]
        for stamp, expected in [
            ('2026-08-24T18:00:00+00:00', '2026-08-24 14:00 EDT'),
            ('2026-01-05T18:00:00+00:00', '2026-01-05 13:00 EST'),
        ]:
            event['timestamp'] = stamp
            before = dict(event)
            props = event_properties(event, 'ticker')
            self.assertEqual(market_time(event), expected)
            self.assertEqual(props['Timestamp']['date']['start'], stamp)
            self.assertEqual(props['Event ID']['rich_text'][0]['text']['content'], event['event_id'])
            self.assertEqual(event, before)

    def test_us_exchange_defaults_use_new_york_time(self):
        for exchange, symbol in [
            ('NASDAQ', 'NVDA'),
            ('NYSE', 'NOW'),
            ('CBOE', 'VIX'),
            ('SP', 'SPX'),
        ]:
            instrument = {'exchange': exchange, 'symbol': symbol}
            self.assertEqual(market_timezone(instrument), 'America/New_York')

    def test_nvidia_and_spx_4h_open_display_as_0930(self):
        for exchange, symbol in [('NASDAQ', 'NVDA'), ('SP', 'SPX')]:
            event = events(backfill=True)[0]
            event.update({
                'exchange': exchange,
                'symbol': symbol,
                'ticker': symbol,
                'timeframe': '4H',
                'timestamp': '2026-09-08T13:30:00+00:00',
                'market_timezone': market_timezone({'exchange': exchange, 'symbol': symbol}),
            })
            self.assertEqual(market_time(event), '2026-09-08 09:30 EDT')

    def test_non_exchange_mapped_market_remains_utc(self):
        self.assertEqual(market_timezone({'exchange': 'BITSTAMP', 'symbol': 'BTCUSD'}), 'UTC')
