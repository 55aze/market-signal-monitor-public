"""Compact evidence, not a second state machine or an investment recommendation."""
from collections import defaultdict
from .state_engine import stamp, TF


def theme_evidence(states, membership, exposure, since, now):
    groups = defaultdict(list)
    for s in states:
        for theme in membership.get(s['ticker'], []):
            groups[theme].append(s)
    result = []
    for theme, members in sorted(groups.items()):
        signals = [dict(e, ticker=s['ticker']) for s in members for e in s['lineage']
                   if e.get('confirmation_at') and stamp(since) < stamp(e['confirmation_at']) <= stamp(now)]
        signals.sort(key=lambda e: (stamp(e['confirmation_at']), e['event_id']))
        directions = {}
        for direction in ('Bottom', 'Sell'):
            matches = [e for e in signals if e['signal'] == direction]
            names = sorted({e['ticker'] for e in matches})
            independent = {exposure[n] for n in names if n in exposure}
            directions[direction] = dict(event_density=len(matches), ticker_breadth=len(names),
                independent_exposure_breadth=len(independent),
                highest_tf=max((e['timeframe'] for e in matches), key=lambda tf:TF[tf], default=None),
                unmapped_tickers=[n for n in names if n not in exposure],
                chronology=[{'ticker':e['ticker'], 'tf':e['timeframe'], 'at':e['confirmation_at']} for e in matches])
        result.append(dict(theme=theme, window={'since':since, 'through':now, 'type':'explicit_timestamp_window'},
            directions=directions, member_count=len(members),
            qualified_or_trend=[s['ticker'] for s in members if s['stage'] in {'Qualified','Opportunity','Trend'}],
            weakening_or_failed=[s['ticker'] for s in members if s['stage'] in {'Weakening','Failed'}],
            unavailable=[s['ticker'] for s in members if s['uncertainty']],
            coverage='engine-observed events only; imported lineage text is not reconstructed'))
    return result


def packet(states, *, now, since, membership=None, exposure=None):
    events = sorted([e for s in states for e in s['pending']],
                    key=lambda e: (e['kind'] != 'OPPORTUNITY', e['at'], e['id']))
    affected = {e['ticker'] for e in events}
    gaps = [{'ticker':s['ticker'], 'reasons':s['uncertainty']} for s in states if s['uncertainty']]
    return dict(version=1, as_of=now, should_report=bool(events),
        new_moves=[e for e in events if e['kind'] == 'HIGHER_TF_SIGNAL' or e.get('to') == 'Signal'],
        state_changes=[e for e in events if e['kind'] != 'HIGHER_TF_SIGNAL' and e.get('to') != 'Signal'],
        affected_states=[{k:s[k] for k in ('ticker','stage','direction','origin_tf','streak',
            'last_bar','highest_bottom_tf','highest_sell_tf','parent_regime','structures','uncertainty')}
            for s in states if s['ticker'] in affected],
        themes=[t for t in theme_evidence(states, membership or {}, exposure or {}, since, now)
                if any(s['ticker'] in affected and t['theme'] in (membership or {}).get(s['ticker'], []) for s in states)],
        data_gaps=gaps, acknowledgement_ids=[e['id'] for e in events],
        lineage_coverage='Imported highest TFs have unverified effective windows; no automatic expiry',
        validation='Python indicators remain TradingView-parity unvalidated')


def publish_packet(client, page_id, data):
    """Patch only Packet. The reporter writes only Delivered IDs: no state race."""
    import json
    from .notion import rich
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    if len(payload) > 175000:
        raise ValueError('Report packet too large; retain pending events and partition delivery')
    client.request('PATCH', f'pages/{page_id}', {'properties': {'Packet': {'rich_text':rich(payload)}}})


def delivery_receipt(client, page_id):
    import json
    from .state_store import plain
    page = client.request('GET', f'pages/{page_id}')
    properties = page['properties']
    if properties.get('Packet', {}).get('type') != 'rich_text' or properties.get('Delivered IDs', {}).get('type') != 'rich_text':
        raise ValueError('Report page requires Packet and Delivered IDs rich_text properties')
    text = plain(properties['Delivered IDs'])
    result = json.loads(text) if text else []
    if not isinstance(result, list) or any(not isinstance(v, str) for v in result):
        raise ValueError('Delivered IDs must be a JSON string array')
    return result
