"""Read-only full-universe audit against the production TradingView provider."""
import json
from pathlib import Path

import pandas as pd

from market_signal_monitor.scanner import run, fetch_worker
from market_signal_monitor.confirmation import confirmation_times, has_close_policy
from market_signal_monitor.freshness import assess_freshness
from audit_history import compare_history


def main():
    config = json.loads(Path('config/universe.json').read_text())
    root = Path('reports/policy-audit')
    root.mkdir(parents=True, exist_ok=True)
    frames = {}

    def fetch(instrument, tf, count):
        bars = fetch_worker(instrument, tf, count)
        frames[instrument['id'], tf] = bars
        # Preserve the previously blocked weekly/young streams for reproducible
        # history-length comparisons without fetching a second time.
        if tf == '1W' or instrument['id'] in ('SPCX', '0100'):
            bars.to_csv(root / (instrument['id'] + '-' + tf + '.csv'))
        return bars

    report = run(config, mode='dry-run', since=config['monitor_since'], fetcher=fetch)
    (root / 'scan.json').write_text(json.dumps(report, indent=2, allow_nan=False))
    instruments = {i['id']: i for i in config['instruments']}
    checks = []
    history_checks = []
    for stream in report['streams']:
        if stream['status'] != 'fetched_and_computed':
            print(json.dumps({'stream': stream['stream'], 'result': stream}))
            continue
        name, tf = stream['stream'].split(':')
        instrument, bars = instruments[name], frames[name, tf]
        if ((tf == '1W' and name in ('QQQ', '588000', 'SPCX', '0100', 'HSTECH', 'SNOW', 'OKLO', 'BE', 'PLTR'))
                or (tf in ('4H', '1D') and name in ('SPCX', '0100'))):
            confirmed = bars.loc[:pd.Timestamp(stream['latest_processed_bar'])]
            evidence = {'stream': stream['stream'], **compare_history(confirmed)}
            history_checks.append(evidence)
            print('HISTORY_AUDIT ' + json.dumps(evidence, allow_nan=False))
        if has_close_policy(instrument):
            since = pd.Timestamp(config['monitor_since'])
            confirms = confirmation_times(bars, instrument, tf, strict_since=since)
            previous = [stamp for stamp, close in zip(bars.index, confirms)
                        if stamp < since and pd.notna(close)]
            check = assess_freshness(bars.index, instrument, tf, pd.Timestamp.now(tz='UTC'),
                                    pd.Timestamp(stream['latest_processed_bar']),
                                    previous_through=previous[-1] if previous else None)
            checks.append({'stream': stream['stream'], **check})
        print(json.dumps({'stream': stream['stream'], 'bars': stream['bars'],
                          'freshness': stream['freshness'], 'history': stream.get('history')}))
    (root / 'continuity.json').write_text(json.dumps(checks, indent=2))
    (root / 'history-comparisons.json').write_text(json.dumps(history_checks, indent=2, allow_nan=False))
    problems = [c for c in checks if c['status'] in ('interior_gap', 'stale_tail')]
    counts = {}
    for stream in report['streams']:
        state = stream.get('freshness', {}).get('status', stream['status'])
        counts[state] = counts.get(state, 0) + 1
    print(json.dumps({'audit_summary': counts, 'errors': report['errors'],
                      'continuity_problems': problems, 'writes': report['created']}))
    return 1 if report['errors'] or problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
