"""Compact evidence, not a second state machine or an investment recommendation."""
from collections import defaultdict
from .snapshot import finite
from .state_engine import stamp, TF, identity


def structure_ladder(timeframe, structure):
    """Render price, EMA200 and both bands from the same confirmed bar, high to low."""
    values = (structure.get('snapshot') or {}).get('values') or {}
    keys = ('Close', 'EMA200', 'blueUpperBand', 'blueLowerBand',
            'yellowUpperBand', 'yellowLowerBand')
    numbers = {key: finite(values.get(key)) for key in keys}
    if (any(value is None for value in numbers.values()) or
            numbers['blueUpperBand'] < numbers['blueLowerBand'] or
            numbers['yellowUpperBand'] < numbers['yellowLowerBand']):
        return f'{timeframe}  — (结构数据不完整)'

    blue_upper, blue_lower = numbers['blueUpperBand'], numbers['blueLowerBand']
    yellow_upper, yellow_lower = numbers['yellowUpperBand'], numbers['yellowLowerBand']
    overlap = max(blue_lower, yellow_lower) <= min(blue_upper, yellow_upper)
    points = (numbers['Close'], numbers['EMA200'])
    blue_open = overlap or any(blue_lower <= point <= blue_upper for point in points)
    yellow_open = overlap or any(yellow_lower <= point <= yellow_upper for point in points)
    levels = [(numbers['Close'], '●'), (numbers['EMA200'], 'E200')]
    for color, upper, lower, expanded in (
            ('🟦', blue_upper, blue_lower, blue_open),
            ('🟨', yellow_upper, yellow_lower, yellow_open)):
        if expanded:
            levels.extend(((upper, color + '顶'), (lower, color + '底')))
        else:
            levels.append(((upper + lower) / 2, color))
    groups = defaultdict(list)
    for value, label in levels:
        groups[value].append(label)
    return f'{timeframe}  ' + ' > '.join(' = '.join(groups[value])
                                             for value in sorted(groups, reverse=True))


def _report_structures(structures):
    return {tf: {**structure, 'ladder': structure_ladder(tf, structure)}
            for tf, structure in structures.items()}


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
    material = [e for e in events if e['kind'] != 'RAW_SIGNAL']
    # Keep the large structure payload for material subjects only. Every raw
    # signal still has its own receipt ID and exact bar/price in the packet.
    affected = {e['ticker'] for e in material}
    gaps = [{'ticker':s['ticker'], 'reasons':s['uncertainty']} for s in states if s['uncertainty']]
    ids = [e['id'] for e in events]
    return dict(version=1, as_of=now, packet_id=identity('packet', sorted(ids)),
        coverage_snapshot={s['ticker']:s.get('coverage', {}) for s in states if s['ticker'] in affected},
        should_report=bool(events),
        raw_signals=[e for e in events if e['kind'] == 'RAW_SIGNAL'],
        new_moves=[e for e in material if e['kind'] == 'HIGHER_TF_SIGNAL' or e.get('to') == 'Signal'],
        state_changes=[e for e in material if e['kind'] != 'HIGHER_TF_SIGNAL' and e.get('to') != 'Signal'],
        affected_states=[{**{k:s[k] for k in ('ticker','stage','direction','origin_tf','streak',
            'last_bar','highest_bottom_tf','highest_sell_tf','parent_regime','uncertainty')},
            'structures':_report_structures(s['structures'])}
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


class DeliveryReceipt(list):
    def __init__(self, ids, packet_id=None, delivered_at=None):
        super().__init__(ids)
        self.packet_id, self.delivered_at = packet_id, delivered_at


def delivery_receipt(client, page_id):
    import json
    import re
    from .state_store import plain
    page = client.request('GET', f'pages/{page_id}')
    properties = page['properties']
    if properties.get('Packet', {}).get('type') != 'rich_text' or properties.get('Delivered IDs', {}).get('type') != 'rich_text':
        raise ValueError('Report page requires Packet and Delivered IDs rich_text properties')
    text = plain(properties['Delivered IDs'])
    if not text:
        return []
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        # Older reporter prompts wrote acknowledgement IDs as comma-separated
        # text. Accept only the exact deterministic event-id shape so arbitrary
        # malformed content cannot acknowledge pending events.
        result = [value.strip() for value in text.split(',')]
        if not result or any(not re.fullmatch(r'[0-9a-f]{24}', value) for value in result):
            raise ValueError('Delivered IDs must be a JSON string array') from None
    packet_id = delivered_at = None
    if isinstance(result, dict):
        packet_id, delivered_at = result.get('packet_id'), result.get('delivered_at')
        result = result.get('item_ids')
        if not isinstance(packet_id, str) or not isinstance(delivered_at, str):
            raise ValueError('Receipt requires packet_id and delivered_at')
        from datetime import datetime, timezone
        if stamp(delivered_at) > datetime.now(timezone.utc):
            raise ValueError('Receipt delivery time is in the future')
        if (not isinstance(result, list) or any(not isinstance(v, str) for v in result)
                or packet_id != identity('packet', sorted(set(result)))):
            raise ValueError('Receipt packet_id does not match item_ids')
    if not isinstance(result, list) or any(not isinstance(v, str) for v in result):
        raise ValueError('Delivered IDs must be a JSON string array')
    return DeliveryReceipt(list(dict.fromkeys(result)), packet_id, delivered_at)
