"""Read back checkpoints; print only public-safe aggregate diagnostics."""
import json
import os
from collections import Counter
from pathlib import Path

import pandas as pd
from market_signal_monitor.notion import Notion


def main():
    report = json.loads(Path('reports/scan.json').read_text())
    streams = report.get('streams', [])
    print(json.dumps({
        'status': report['status'], 'streams': len(streams),
        'stream_statuses': dict(Counter(s['status'] for s in streams)),
        'duration_seconds': report.get('duration_seconds'),
        'delivery_seconds': report.get('delivery_seconds'),
        'fetch_seconds': round(sum(s.get('fetch_seconds', 0) for s in streams), 3),
        'compute_seconds': round(sum(s.get('compute_seconds', 0) for s in streams), 3),
        'created': report.get('created'), 'existing': report.get('existing'),
        'errors': len(report.get('errors', [])),
        'warning_kinds': dict(Counter(w.get('kind', 'other') for w in report.get('warnings', []))),
        'curve_timeframes': sorted(report.get('curve_latest', {})),
        'event_curve_statuses': dict(Counter(e.get('curve_status', 'missing') for e in report.get('events', []))),
        'AMZN_streams': {s['stream']: s['status'] for s in streams if s['stream'].startswith('AMZN:')},
    }))
    if report.get('mode') != 'live':
        return
    config = json.loads(Path('config/universe.json').read_text())
    expected = {f"{i['id']}:{tf}" for i in config['instruments']
                for tf in i.get('timeframes', config['timeframes'])}
    if {s['stream'] for s in streams} != expected or len(streams) != len(expected):
        raise ValueError('Unexpected scan scope')
    n = config['notion']
    client = Notion(os.environ['NOTION_TOKEN'], n['events_data_source'], n['tickers_data_source'])
    rows = {}
    for row in client.query(n['status_data_source'], {'property': 'Stream', 'title': {'is_not_empty': True}}):
        props = row['properties']
        key = ''.join(t.get('plain_text', t.get('text', {}).get('content', '')) for t in props['Stream']['title'])
        if key not in expected:
            continue
        if key in rows:
            raise ValueError('Duplicate status stream')
        rows[key] = props
    verified = 0
    for s in streams:
        if s['status'] not in ('fetched_and_computed', 'skipped_not_due'):
            continue
        props = rows[s['stream']]
        actual = props['Continuous Through']['date']
        if not actual or pd.Timestamp(actual['start']) != pd.Timestamp(s['latest_processed_bar']):
            raise ValueError('Checkpoint readback mismatch')
        if s['status'] == 'fetched_and_computed':
            success = props['Last Success']['date']
            if not success or pd.Timestamp(success['start']) < pd.Timestamp(report['run_at']):
                raise ValueError('Last Success did not advance')
            if props['Scan Status']['select']['name'] != 'Success':
                raise ValueError('Stream write not successful')
        verified += 1
    print(json.dumps({'configured_streams': len(expected), 'notion_checkpoints_verified': verified}))
    if report.get('errors'):
        raise ValueError('Scan reported errors')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Verification failed:', type(exc).__name__)
        raise SystemExit(1)
