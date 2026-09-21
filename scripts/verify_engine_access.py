"""Read-only production access check; never print private source IDs or rows."""
import json
import os
from pathlib import Path
from market_signal_monitor.notion import Notion
from market_signal_monitor.state_store import TickerStore
from market_signal_monitor.report_packet import delivery_receipt

config=json.loads(Path('config/universe.json').read_text())
e=config.get('ticker_engine')
if e:
    n=config['notion']
    client=Notion(os.environ['NOTION_TOKEN'],n['events_data_source'],n['tickers_data_source'])
    try:
        store=TickerStore(client,e['state_data_source'])
        store.preflight()
        delivery_receipt(client,e['report_page'])
        expected={i.get('notion_ticker',i['id']) for i in config['instruments']}
        missing=expected-set(store.rows)
        if missing:
            print(json.dumps({'engine_preflight':'ticker_mapping_missing','count':len(missing)}))
            raise SystemExit(1)
        print(json.dumps({'engine_preflight':'ok','tickers':len(expected),'enabled':e['enabled']}))
    except (RuntimeError,ValueError) as exc:
        # Status only; request path/IDs are deliberately suppressed.
        message=str(exc)
        print(json.dumps({'engine_preflight':'failed','kind':type(exc).__name__,
                          'http_status':next((s for s in ('400','401','403','404') if 'HTTP '+s in message),None)}))
        raise SystemExit(1)
