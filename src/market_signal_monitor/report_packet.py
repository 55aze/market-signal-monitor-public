"""Compact evidence, not a second state machine or an investment recommendation."""
from collections import defaultdict
import json
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
    return {tf: {**{key: structure[key] for key in ('timestamp', 'confirmed_at', 'value_unit')
                   if key in structure}, 'ladder': structure_ladder(tf, structure)}
            for tf, structure in structures.items() if tf in TF}


def _report_coverage(coverage):
    return {tf: {key: evidence[key] for key in (
        'status', 'checked_at', 'expected_latest', 'actual_latest', 'missing_count')
        if key in evidence} for tf, evidence in coverage.items() if tf in TF}


def _report_theme(theme):
    return {**{key: theme[key] for key in ('theme', 'window', 'member_count', 'coverage')},
            'directions': {direction: {
                **{key: evidence[key] for key in ('event_density', 'ticker_breadth',
                   'independent_exposure_breadth', 'highest_tf')},
                'unmapped_ticker_count': len(evidence['unmapped_tickers'])}
                for direction, evidence in theme['directions'].items()},
            **{key + '_count': len(theme[key]) for key in (
                'qualified_or_trend', 'weakening_or_failed', 'unavailable')}}


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


# A conservative reporter read budget, not Notion's much larger storage limit.
# Connector readback still needs an actual scheduled-task validation.
MAX_PACKET_CHARS = 12000
MAX_BATCH_ITEMS = 25
MAX_CONTEXT_TICKERS = 5
MAX_DIAGNOSTIC_ITEMS = 5
MAX_REPORT_THEMES = 2


class PacketSerializationError(ValueError):
    pass


def _encode(data):
    try:
        return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
    except (TypeError, ValueError) as exc:
        raise PacketSerializationError('Report packet is not finite JSON') from exc


class PacketBudgetError(ValueError):
    def __init__(self, data, limit):
        super().__init__('Report packet cannot fit one item; pending retained')
        self.diagnostics = packet_diagnostics(data, limit)


def packet_diagnostics(data, limit=MAX_PACKET_CHARS):
    return dict(chars=len(_encode(data)), limit=limit,
                items=len(data.get('acknowledgement_ids', [])),
                remaining=data.get('batch', {}).get('remaining', 0))


def _report_line(event):
    if event['kind'] == 'RAW_SIGNAL':
        return (f"{event['ticker']} · {event.get('origin_tf', 'unknown')} "
                f"{event.get('signal', 'unknown')} · {event.get('price')} "
                f"{event.get('value_unit', 'Price')} · bar_at={event.get('bar_at')} · "
                f"confirmed_at={event.get('confirmed_at')}")
    change = f"{event['from']} → {event['to']}" if 'from' in event and 'to' in event else ''
    return (f"{event['ticker']} · {event['kind']} {change} · "
            f"{event.get('origin_tf', '')} {event.get('direction', '')} · at={event['at']} · "
            f"{event.get('reason', '')}").rstrip(' ·')


def packet(states, *, now, since, membership=None, exposure=None,
           max_items=MAX_BATCH_ITEMS, max_chars=MAX_PACKET_CHARS,
           operational_errors=None, coverage=None):
    """Publish an oldest-first prefix; only acknowledgement consumes the outbox."""
    if type(max_items) is not int or max_items < 1 or type(max_chars) is not int or not 0 < max_chars <= MAX_PACKET_CHARS:
        raise ValueError('Invalid packet budget')
    events = sorted([e for s in states for e in s['pending']],
                    key=lambda e: (stamp(e['at']), e['id']))
    # Bounded work: at most max_items candidates. Rebuild all associated context
    # for each prefix; never omit an event while acknowledging its ID.
    for count in range(min(len(events), max_items), -1, -1):
        if events and count == 0:
            break
        data = _packet(states, events[:count], now=now, since=since,
                       membership=membership, exposure=exposure)
        data['batch'] = dict(total_pending=len(events), included=count,
                             remaining=len(events) - count, order='oldest_first')
        if operational_errors is not None:
            data['operational_errors'] = operational_errors[:MAX_DIAGNOSTIC_ITEMS]
            data['context_omitted']['operational_errors'] = max(
                0, len(operational_errors) - MAX_DIAGNOSTIC_ITEMS)
            data['should_report'] = data['should_report'] or bool(operational_errors)
        if coverage is not None:
            data['coverage'] = coverage
        # Keep this last so a partial tool response cannot masquerade as a
        # complete batch merely because its header and item count are visible.
        data['packet_end'] = data['packet_id']
        if len(_encode(data)) <= max_chars:
            return data
    raise PacketBudgetError(data, max_chars)


def _packet(states, events, *, now, since, membership=None, exposure=None):
    material = [e for e in events if e['kind'] != 'RAW_SIGNAL']
    # Full snapshots and lineage remain in Engine State. The reporter needs
    # deterministic item lines and compact current context, not duplicate events.
    affected = {e['ticker'] for e in material}
    subjects = sorted((s for s in states if s['ticker'] in affected), key=lambda s: s['ticker'])
    context = subjects[:MAX_CONTEXT_TICKERS]
    gaps = [{'ticker':s['ticker'], 'reasons':s['uncertainty']}
            for s in sorted(states, key=lambda s: s['ticker']) if s['uncertainty']]
    themes = [_report_theme(t) for t in theme_evidence(
        states, membership or {}, exposure or {}, since, now)
        if any(t['theme'] in (membership or {}).get(ticker, []) for ticker in affected)]
    ids = [e['id'] for e in events]
    return dict(version=2, as_of=now, packet_id=identity('packet', sorted(ids)),
        report_items=[{'id': e['id'], 'kind': e['kind'], 'line': _report_line(e)} for e in events],
        acknowledgement_ids=ids,
        coverage_snapshot={s['ticker']:_report_coverage(s.get('coverage', {})) for s in context},
        should_report=bool(events),
        affected_states=[{**{k:s[k] for k in ('ticker','stage','direction','origin_tf','streak',
            'last_bar','highest_bottom_tf','highest_sell_tf','parent_regime','uncertainty')},
            'structures':_report_structures(s['structures'])}
            for s in context],
        themes=themes[:MAX_REPORT_THEMES], data_gaps=gaps[:MAX_DIAGNOSTIC_ITEMS],
        context_omitted={'affected_states':len(subjects) - len(context),
                         'themes':max(0, len(themes) - MAX_REPORT_THEMES),
                         'data_gaps':max(0, len(gaps) - MAX_DIAGNOSTIC_ITEMS)},
        lineage_coverage='Imported highest TFs have unverified effective windows; no automatic expiry',
        validation='Python indicators remain TradingView-parity unvalidated')


def publish_packet(client, page_id, data):
    """Patch only Packet. The reporter writes only Delivered IDs: no state race."""
    from .notion import rich
    payload = _encode(data)
    if len(payload) > MAX_PACKET_CHARS:
        raise PacketBudgetError(data, MAX_PACKET_CHARS)
    client.request('PATCH', f'pages/{page_id}', {'properties': {'Packet': {'rich_text':rich(payload)}}})


class DeliveryReceipt(list):
    def __init__(self, ids, packet_id=None, delivered_at=None, delivery_evidence=None, report_ref=None):
        super().__init__(ids)
        self.packet_id, self.delivered_at = packet_id, delivered_at
        self.delivery_evidence, self.report_ref = delivery_evidence, report_ref


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
    packet_id = delivered_at = delivery_evidence = report_ref = None
    if isinstance(result, dict):
        packet_id, delivered_at = result.get('packet_id'), result.get('delivered_at')
        delivery_evidence, report_ref = result.get('delivery_evidence'), result.get('report_ref')
        if not isinstance(packet_id, str) or 'delivered_at' not in result:
            raise ValueError('Receipt requires packet_id and explicit delivered_at')
        if report_ref is not None and (not isinstance(report_ref, str) or not report_ref.strip()):
            raise ValueError('Invalid report reference')
        if delivered_at is None:
            if delivery_evidence != 'prior_complete_report':
                raise ValueError('Unknown delivery time requires prior complete report evidence')
        elif not isinstance(delivered_at, str):
            raise ValueError('Invalid delivery timestamp')
        else:
            from datetime import datetime, timezone
            if stamp(delivered_at) > datetime.now(timezone.utc):
                raise ValueError('Receipt delivery time is in the future')
        result = result.get('item_ids')
        if (not isinstance(result, list) or any(not isinstance(v, str) for v in result)
                or packet_id != identity('packet', sorted(set(result)))):
            raise ValueError('Receipt packet_id does not match item_ids')
    if not isinstance(result, list) or any(not isinstance(v, str) for v in result):
        raise ValueError('Delivered IDs must be a JSON string array')
    return DeliveryReceipt(list(dict.fromkeys(result)), packet_id, delivered_at, delivery_evidence, report_ref)
