"""Private reporter packet export and explicit delivery acknowledgement.

Run ack only after successful report delivery, through the SAME serialized writer
as the scanner. Never run ack concurrently with scanner state writes.
"""
import argparse
import json
import os
from pathlib import Path
from .notion import Notion
from .state_store import TickerStore
from .state_engine import acknowledge, stamp
from .report_packet import packet


def main():
    p = argparse.ArgumentParser()
    p.add_argument('action', choices=['packet', 'ack'])
    p.add_argument('--config', default='config/universe.json')
    p.add_argument('--now', required=True)
    p.add_argument('--since', required=True)
    p.add_argument('--output', default='reports/watch-packet.json')
    p.add_argument('--delivered-packet', help='Exact previously delivered packet to acknowledge')
    args = p.parse_args()
    if stamp(args.since) >= stamp(args.now):
        raise ValueError('Invalid report window')
    config = json.loads(Path(args.config).read_text())
    n = config['notion']
    store = TickerStore(Notion(os.environ['NOTION_TOKEN'], n['events_data_source'], n['tickers_data_source']),
                        config['ticker_engine']['state_data_source'])
    store.preflight()
    states = [store.load(ticker, args.now) for ticker in store.rows]
    if args.action == 'ack':
        if not args.delivered_packet:
            raise ValueError('Acknowledgement requires the exact delivered packet')
        ids = json.loads(Path(args.delivered_packet).read_text())['acknowledgement_ids']
        for state in states:
            updated = acknowledge(state, ids)
            if updated != state:
                store.save(state['ticker'], updated)
    else:
        data = packet(states, now=args.now, since=args.since,
                      membership=config.get('theme_membership'), exposure=config.get('exposure_groups'))
        target = Path(args.output)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False))
        target.chmod(0o600)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
