"""Provision missing AMZN rows without exposing runtime configuration."""
import json
import os
from pathlib import Path
from market_signal_monitor.enrollment import enroll_amzn
from market_signal_monitor.notion import Notion

try:
    config = json.loads(Path('config/universe.json').read_text())
    n = config['notion']
    client = Notion(os.environ['NOTION_TOKEN'], n['events_data_source'], n['tickers_data_source'])
    print('AMZN status rows created:', enroll_amzn(config, client))
except Exception as exc:
    print('AMZN enrollment failed:', type(exc).__name__)
    raise SystemExit(1)
