"""Read-only history truncation experiment; comparisons are evidence, not certification."""
import argparse
import json
import numpy as np
from market_signal_monitor.data import read_bars
from market_signal_monitor.indicator import calculate
from market_signal_monitor.history import dependency_masks
from market_signal_monitor.snapshot import snapshot


def compare_history(bars):
    full = calculate(bars)
    baseline = snapshot(bars.close.iloc[-1], full.iloc[-1])
    columns = ['ema20', 'ema50', 'ema100', 'ema200', 'blueUpperBand',
               'blueLowerBand', 'yellowUpperBand', 'yellowLowerBand']
    rows = []
    for size in sorted({len(bars), *[n for n in (500, 300, 200, 100, 50) if n < len(bars)]}, reverse=True):
        subset = bars.iloc[-size:]
        calc = full if size == len(bars) else calculate(subset)
        current = snapshot(subset.close.iloc[-1], calc.iloc[-1])
        masks = dependency_masks(calc)
        overlap = calc.index[-min(30, size):]
        delta = calc.iloc[-1][columns] - full.iloc[-1][columns]
        denom = full.iloc[-1][columns].abs().replace(0, np.nan)
        relative = delta / denom * 100
        rows.append({'bars': size, 'history_start': subset.index[0].isoformat(),
            'line_delta': {k: float(v) for k, v in delta.items()},
            'line_delta_pct': {k: float(v) if np.isfinite(v) else None for k, v in relative.items()},
            'same_price_positions': (current['ema_position'] == baseline['ema_position']
                                     and current['channel_position'] == baseline['channel_position']),
            'latest_price_positions': {'ema': current['ema_position'], 'channels': current['channel_position']},
            'signal_mismatch_last_30': {k: int((calc.loc[overlap, k] != full.loc[overlap, k]).sum())
                                      for k in ('DXDX', 'DBJGXC')},
            'dependencies_ready_latest': {k: bool(masks.iloc[-1][k]) for k in masks}})
    return {'returned_bars': len(bars), 'comparison': rows,
            'limitation': 'Longest fetched history is the reference, not ground truth; latest row may be unconfirmed.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    args = parser.parse_args()
    print(json.dumps(compare_history(read_bars(args.csv)), indent=2, allow_nan=False))
