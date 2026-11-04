"""Read-only replay of native CSV bars; never instantiates a production client.

Checkpoint and now are explicit UTC bounds supplied by the operator. Output is
local diagnostics, not a replacement for a live scan or delivery receipt.
"""
import argparse
import json
from pathlib import Path
import pandas as pd
from market_signal_monitor.data import read_bars
from market_signal_monitor.confirmation import confirmation_times
from market_signal_monitor.freshness import assess_freshness


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path)
    parser.add_argument('--instrument', required=True)
    parser.add_argument('--timeframe', required=True, choices=['30m', '4H', '1D', '1W'])
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--now', required=True)
    parser.add_argument('--strict-since', required=True,
                        help='Original monitor_since; do not narrow to hide historical validation failures')
    args = parser.parse_args()
    public = json.loads((Path(__file__).resolve().parents[1] / 'config/universe.example.json').read_text())
    instrument = next(i for i in public['instruments'] if i['id'] == args.instrument)
    bars = read_bars(args.csv)
    checkpoint, now, strict_since = map(pd.Timestamp, (args.checkpoint, args.now, args.strict_since))
    if any(t.tzinfo is None for t in (checkpoint, now, strict_since)):
        raise ValueError('All bounds must be timezone-aware')
    if strict_since > checkpoint or checkpoint > now:
        raise ValueError('Require strict_since <= checkpoint <= now')
    if checkpoint not in bars.index:
        raise ValueError('Durable checkpoint absent from native history')
    confirmations = confirmation_times(bars, instrument, args.timeframe, strict_since=strict_since)
    latest = max((stamp for stamp, close in zip(bars.index, confirmations) if close <= now), default=pd.NaT)
    if pd.isna(latest) or latest < checkpoint:
        raise ValueError('No confirmed coverage through checkpoint')
    result = assess_freshness(bars.index, instrument, args.timeframe, now, latest, checkpoint)
    print(json.dumps(result, indent=2))
    return 0 if result['status'] == 'current' else 1


if __name__ == '__main__':
    raise SystemExit(main())
