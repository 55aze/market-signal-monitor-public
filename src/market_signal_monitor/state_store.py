"""Single-writer Notion persistence. One PATCH holds state + transition outbox.

Migration is explicit: existing ticker rows are imported, never recreated. The
Engine State rich_text property must be provisioned before enabling the engine.
"""
import json
from .notion import rich
from .state_engine import seed, VERSION


def plain(prop):
    kind = prop['type']
    value = prop.get(kind)
    if kind in {'rich_text', 'title'}:
        return ''.join(t.get('plain_text', t.get('text', {}).get('content', '')) for t in value)
    if kind == 'select':
        return value['name'] if value else 'None'
    if kind == 'date':
        return value['start'] if value else None
    return value


class TickerStore:
    def __init__(self, client, source):
        self.client, self.source = client, source
        self.rows = {}

    def preflight(self):
        schema = self.client.request('GET', f'data_sources/{self.source}')['properties']
        required = {'Ticker': 'title', 'Engine State': 'rich_text', 'Stage': 'select',
                    'Direction': 'select', 'Origin TF': 'select', 'Blue Streak Bars': 'number',
                    'Last Origin Bar': 'date', 'Last Transition': 'date', 'State Version': 'rich_text',
                    'Attention': 'select', 'Retest': 'select', 'Highest Bottom TF': 'select',
                    'Highest Sell TF': 'select', 'Origin Event At': 'date'}
        for key, kind in required.items():
            if schema.get(key, {}).get('type') != kind:
                raise ValueError(f'Ticker state schema missing {key} ({kind})')
        for row in self.client.query(self.source, {'property': 'Ticker', 'title': {'is_not_empty': True}}):
            props = row['properties']
            ticker = plain(props['Ticker'])
            if ticker in self.rows:
                raise ValueError('Duplicate ticker state row')
            self.rows[ticker] = row

    def load(self, ticker, now):
        if ticker not in self.rows:
            raise ValueError('Ticker state row missing; enroll explicitly before rollout')
        row = self.rows[ticker]
        encoded = plain(row['properties']['Engine State'])
        if encoded:
            result = json.loads(encoded)
            if result.get('version') != VERSION or result.get('ticker') != ticker:
                raise ValueError('Ticker state version/identity mismatch')
            return result
        baseline = {}
        for key, prop in row['properties'].items():
            baseline['date:' + key + ':start' if prop['type'] == 'date' else key] = plain(prop)
        return seed(baseline, activated_at=now)

    def save(self, ticker, state):
        payload = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        # Notion arrays have 100-element limits. Never truncate history/outbox.
        if len(payload) > 175000:
            raise ValueError('Engine State requires archival before further progression')
        props = {'Engine State': {'rich_text': rich(payload)},
                 'State Version': {'rich_text': rich(VERSION)},
                 'Blue Streak Bars': {'number': state['streak']}}
        for key, field in {'Stage':'stage', 'Direction':'direction', 'Origin TF':'origin_tf',
                           'Attention':'attention', 'Retest':'retest',
                           'Highest Bottom TF':'highest_bottom_tf', 'Highest Sell TF':'highest_sell_tf'}.items():
            props[key] = {'select': {'name': state[field]}}
        for key, field in {'Last Origin Bar':'last_bar', 'Last Transition':'last_transition',
                           'Origin Event At':'origin_at'}.items():
            props[key] = {'date': {'start': state[field]} if state[field] else None}
        self.client.request('PATCH', f"pages/{self.rows[ticker]['id']}", {'properties': props})
