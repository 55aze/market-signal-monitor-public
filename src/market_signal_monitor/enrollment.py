"""Idempotent status-row enrollment under the scanner's single-writer lock."""
from .notion import rich
from .backfill import StatusStore


def enroll_amzn(config, client):
    instruments = [i for i in config['instruments'] if i['id'] == 'AMZN']
    if len(instruments) != 1:
        raise ValueError('Expected exactly one AMZN instrument')
    instrument = instruments[0]
    client.preflight()
    source = config['notion']['status_data_source']
    StatusStore(client, source).preflight()
    schema = client.request('GET', f'data_sources/{source}')['properties']
    ticker = client.ticker_id('AMZN')
    created = 0
    for timeframe in instrument.get('timeframes', config['timeframes']):
        stream = f'AMZN:{timeframe}'
        rows = list(client.query(source, {'property': 'Stream', 'title': {'equals': stream}}))
        if len(rows) > 1:
            raise ValueError('Duplicate AMZN status rows')
        if rows:
            continue
        props = {'Stream': {'title': rich(stream)}, 'Scan Status': {'select': {'name': 'Paused'}}}
        if schema.get('Ticker', {}).get('type') == 'relation':
            props['Ticker'] = {'relation': [{'id': ticker}]}
        if schema.get('Timeframe', {}).get('type') == 'select':
            props['Timeframe'] = {'select': {'name': timeframe}}
        # Notion.request does not retry ambiguous creates. Next run reconciles by Stream.
        client.request('POST', 'pages', {'parent': {'data_source_id': source}, 'properties': props})
        created += 1
    return created
