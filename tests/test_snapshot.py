import json
import unittest
import numpy as np
import pandas as pd
from market_signal_monitor.snapshot import snapshot, notion_properties
from market_signal_monitor.events import extract_events
from market_signal_monitor.indicator import calculate


class SnapshotTests(unittest.TestCase):
    def test_exact_order_and_distances(self):
        s = snapshot(105, dict(ema20=103, ema50=100, ema100=110, ema200=120,
                              blueUpperBand=106, blueLowerBand=104,
                              yellowUpperBand=119, yellowLowerBand=109))
        self.assertEqual(s['order'], 'EMA200 > EMA100 > Close > EMA20 > EMA50')
        self.assertEqual(s['alignment'], 'Mixed')
        self.assertAlmostEqual(s['distance_pct']['20'], 1.9417475728155)
        self.assertAlmostEqual(s['distance_pct']['200'], -12.5)
        self.assertEqual(s['channel_position'], {'blue': 'Inside', 'yellow': 'Below'})
        props = notion_properties(s)
        self.assertEqual(props['EMA 200']['number'], 120)
        self.assertEqual(props['Close vs EMA 100']['select']['name'], 'Below')
        self.assertAlmostEqual(props['Distance EMA 50 %']['number'], 5)

    def test_equality_boundaries_and_invalid_values(self):
        row = dict(ema20=10, ema50=10, ema100=10, ema200=10,
                   blueUpperBand=10, blueLowerBand=9, yellowUpperBand=11, yellowLowerBand=10)
        s = snapshot(10, row)
        self.assertEqual(s['order'], 'Close = EMA20 = EMA50 = EMA100 = EMA200')
        self.assertEqual(s['channel_position'], {'blue': 'Inside', 'yellow': 'Inside'})
        self.assertEqual(s['ema_position']['20'], 'Equal')
        row.update(ema20=float('nan'), ema50=0, ema100=float('inf'), yellowLowerBand=12)
        s = snapshot(10, row)
        self.assertIsNone(s['order'])
        self.assertIsNone(s['distance_pct']['50'])
        self.assertEqual(s['channel_position']['yellow'], 'Unavailable')
        json.dumps(notion_properties(s), allow_nan=False)

    def test_all_channel_positions_and_alignment(self):
        row = dict(ema20=4, ema50=3, ema100=2, ema200=1,
                   blueUpperBand=4, blueLowerBand=2, yellowUpperBand=6, yellowLowerBand=5)
        self.assertEqual(snapshot(7, row)['channel_position'], {'blue': 'Above', 'yellow': 'Above'})
        self.assertEqual(snapshot(1, row)['channel_position']['blue'], 'Below')
        self.assertEqual(snapshot(1, row)['alignment'], 'Bullish')
        row.update(ema20=1, ema50=2, ema100=3, ema200=4)
        self.assertEqual(snapshot(1, row)['alignment'], 'Bearish')

    def test_snapshot_uses_signal_bar_and_survives_future_bars(self):
        index = pd.date_range('2025-01-01', periods=100, freq='h', tz='UTC')
        prices = np.linspace(100, 150, 100)
        bars = pd.DataFrame({'open': prices, 'close': prices, 'high': prices+2, 'low': prices-2}, index=index)
        short = calculate(bars.iloc[:80])
        full = calculate(bars)
        # Force one event to isolate historical snapshot extraction from signal frequency.
        for frame in (short, full):
            frame['DXDX'] = 0
            frame['DBJGXC'] = 0
            frame.loc[index[60], 'DXDX'] = 1
        args = ({'id': 'TEST', 'exchange': 'TEST', 'symbol': 'TEST'}, '1H', 'unchanged')
        a, = extract_events(bars.iloc[:80], short, *args, index[79], backfill=True, warmup=1)
        b, = extract_events(bars, full, *args, index[99], backfill=True, warmup=1)
        self.assertEqual(a['event_id'], b['event_id'])
        self.assertEqual(a['snapshot'], b['snapshot'])
        self.assertEqual(a['snapshot']['values']['EMA20'], float(short.loc[index[60], 'ema20']))
        self.assertNotEqual(a['snapshot']['values']['EMA20'], float(short.iloc[-1].ema20))
        self.assertEqual(a['snapshot']['values']['Close'], float(bars.loc[index[60], 'close']))
