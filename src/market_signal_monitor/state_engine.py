"""Deterministic ticker lifecycle; no network, resampling, or LLM state ownership.

Input bars must be a contiguous confirmed native-timeframe sequence. The adapter
checks continuity against the durable origin checkpoint before calling advance.
All transitions and unacknowledged notifications travel with the state atomically.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import math

TF = {'30m': 0, '4H': 1, '1D': 2, '1W': 3}
VERSION = 'ticker-engine-v1'


def stamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError('Timezone required')
    return result.astimezone(timezone.utc)


def identity(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()[:24]


def policy(overrides=None):
    p = {'acceptance': {'30m': 5, '4H': 4, '1D': 3, '1W': 3}}
    if overrides:
        if set(overrides) - set(p):
            raise ValueError('Unknown lifecycle policy')
        p.update(deepcopy(overrides))
    if set(p['acceptance']) != set(TF) or any(type(v) is not int or v < 1 for v in p['acceptance'].values()):
        raise ValueError('Each timeframe needs a positive integer acceptance threshold')
    return p


def seed(row, activated_at=None):
    origin = row.get('date:Origin Event At:start')
    tf, direction = row.get('Origin TF', 'None'), row.get('Direction', 'None')
    ticker = row['Ticker']
    return dict(version=VERSION, ticker=ticker, stage=row.get('Stage', 'No active setup'),
                direction=direction, origin_tf=tf, origin_at=origin,
                setup_id=identity(ticker, tf, direction, origin), origin_confirmed_at=origin,
                streak=int(row.get('Blue Streak Bars') or 0),
                last_bar=row.get('date:Last Origin Bar:start'),
                last_transition=row.get('date:Last Transition:start'), last_confirmed_at=None,
                highest_bottom_tf=row.get('Highest Bottom TF', tf if direction == 'Bottom' else 'None'),
                highest_sell_tf=row.get('Highest Sell TF', tf if direction == 'Sell' else 'None'),
                lineage_text=row.get('Lineage', ''), lineage=[], seen=[], history=[], pending=[],
                attention=row.get('Attention', 'Watch'), retest=row.get('Retest', 'None'),
                structure=row.get('Structure', ''), structures={}, stream_errors={},
                parent_regime=row.get('Parent Regime', 'Unknown'),
                theme_support=row.get('Theme Support', 'None'),
                retest_close=None, previous_close=None, uncertainty=[],
                signal_cutoff=activated_at or origin, imported=True)


def _transition(s, stage, at, reason, notify=True):
    if stage == s['stage']:
        return
    before = s['stage']
    event = dict(id=identity(s['setup_id'], before, stage, at, reason),
                 ticker=s['ticker'], direction=s['direction'], origin_tf=s['origin_tf'],
                 at=at, kind='OPPORTUNITY' if stage == 'Opportunity' else
                 'FAILURE' if stage == 'Failed' else 'TICKER_STAGE_CHANGE',
                 **{'from': before, 'to': stage}, reason=reason)
    s['history'].append(event)
    if notify:
        s['pending'].append(event)
    s['stage'], s['last_transition'] = stage, at
    s['attention'] = ('Priority' if stage in {'Qualified', 'Opportunity', 'Weakening', 'Failed'}
                      else 'Background' if stage == 'Trend' else
                      'None' if stage == 'No active setup' else 'Watch')


def _signal(s, e, p):
    if e['event_id'] in s['seen']:
        return
    s['seen'].append(e['event_id'])
    s['lineage'].append({k: e.get(k) for k in
        ('event_id', 'timeframe', 'signal', 'timestamp', 'confirmation_at', 'price', 'value_unit')})
    key = 'highest_bottom_tf' if e['signal'] == 'Bottom' else 'highest_sell_tf'
    if TF[e['timeframe']] > TF.get(s[key], -1):
        s[key] = e['timeframe']
    new_rank, old_rank = TF[e['timeframe']], TF.get(s['origin_tf'], -1)
    opposite = e['signal'] != s['direction']
    active = s['stage'] not in {'Failed', 'No active setup'} and old_rank >= 0
    # One isolated 30m event is context, not an automatically actionable setup.
    adopt = (new_rank >= 1 and (not active or new_rank > old_rank or
                              (new_rank == old_rank and opposite)))
    adopt = adopt and (not s.get('origin_confirmed_at') or
                       stamp(e['confirmation_at']) > stamp(s['origin_confirmed_at']))
    if adopt and s.get('last_confirmed_at') and stamp(e['confirmation_at']) < stamp(s['last_confirmed_at']):
        # A delayed signal must not retrospectively erase already-persisted success.
        s['uncertainty'] = ['Late dominant signal requires explicit chronological replay']
        return
    if adopt:
        if active and opposite:
            _transition(s, 'Failed', e['confirmation_at'], 'Comparable or higher timeframe opposite signal')
        s.update(direction=e['signal'], origin_tf=e['timeframe'], origin_at=e['timestamp'],
                 origin_confirmed_at=e['confirmation_at'],
                 setup_id=identity(s['ticker'], e['event_id']), streak=0, last_bar=None,
                 previous_close=None, retest='None', retest_close=None)
        # A new setup is a new lifecycle, even if its first stage has the same name.
        s['stage'] = 'No active setup'
        _transition(s, 'Signal', e['confirmation_at'], 'New higher-timeframe setup')
        s['pending'][-1].update(price=e.get('price'), bar_at=e['timestamp'], value_unit=e.get('value_unit', 'Price'))
    elif new_rank >= 1:
        event = dict(id=identity(e['event_id'], 'HIGHER_TF_SIGNAL'), kind='HIGHER_TF_SIGNAL',
                     ticker=s['ticker'], direction=e['signal'], origin_tf=e['timeframe'],
                     at=e['confirmation_at'], price=e.get('price'), bar_at=e['timestamp'],
                     value_unit=e.get('value_unit','Price'),
                     reason='New higher-timeframe signal; existing origin retained')
        s['pending'].append(event)


def _bar(s, b, p):
    if b['timeframe'] != s['origin_tf'] or s['direction'] not in {'Bottom', 'Sell'}:
        return
    if s['last_bar'] and stamp(b['timestamp']) <= stamp(s['last_bar']):
        return
    if s['origin_at'] and stamp(b['timestamp']) < stamp(s['origin_at']):
        return
    values = [b.get(k) for k in ('close', 'blue_lower', 'blue_upper')]
    if any(not isinstance(x, (int, float)) or not math.isfinite(x) for x in values) or values[1] > values[2]:
        raise ValueError('Missing or invalid Blue bounds/close')
    c, lo, hi = values
    bottom = s['direction'] == 'Bottom'
    accepted = c > hi if bottom else c < lo
    broken = c < lo if bottom else c > hi
    s['streak'] = s['streak'] + 1 if accepted else 0
    at = b['confirmed_at']
    stage = s['stage']
    if stage in {'Signal', 'Developing'}:
        if s['streak'] >= p['acceptance'][s['origin_tf']]:
            _transition(s, 'Qualified', at, 'Distinct confirmed origin closes accepted beyond Blue')
        elif (c >= lo if bottom else c <= hi):
            _transition(s, 'Developing', at, 'Origin close entered or reclaimed Blue')
    elif stage in {'Qualified', 'Opportunity', 'Trend'}:
        if broken:
            s['before_weakening'] = stage
            s['retest'] = 'Failed'
            _transition(s, 'Weakening', at, 'Origin close broke the defended Blue boundary')
        elif bottom and stage in {'Qualified', 'Trend'} and lo <= c <= hi:
            # Conservative v1: close returns INSIDE Blue after acceptance. Wicks
            # alone and closes still above Blue do not create an opportunity.
            s['retest'], s['retest_close'] = 'Held', c
            _transition(s, 'Opportunity', at, 'Post-acceptance pullback close inside Blue held Blue Lower')
        elif bottom and stage == 'Opportunity' and s['streak'] >= p['acceptance'][s['origin_tf']]:
            _transition(s, 'Trend', at, 'Post-retest origin closes re-established acceptance above Blue', notify=False)
    elif stage == 'Weakening':
        if broken:
            _transition(s, 'Failed', at, 'Next distinct origin close confirmed structural failure')
        elif accepted:
            _transition(s, 'Developing', at, 'Recovered boundary; acceptance must rebuild')
            s['retest'] = 'None'
    s['last_bar'], s['previous_close'] = b['timestamp'], c
    s['last_confirmed_at'] = b['confirmed_at']


def advance(state, bars, signals, now, rules=None):
    """Replay by confirmation time; input is never mutated. Missing data freezes
    the remainder of this origin's replay instead of fabricating a continuous streak.
    """
    p, s, cutoff = policy(rules), deepcopy(state), stamp(now)
    items = []
    for e in signals:
        at = e.get('confirmation_at') or e.get('next_bar_at')
        if (e.get('backfill') or e.get('ticker') != s['ticker'] or
                e.get('timeframe') not in TF or e.get('signal') not in {'Bottom', 'Sell'} or not at):
            continue
        if stamp(at) > cutoff or (s['signal_cutoff'] and stamp(at) <= stamp(s['signal_cutoff'])):
            continue
        items.append((stamp(at), 0, e['event_id'], {**e, 'confirmation_at': at}))
    for b in bars:
        if stamp(b['confirmed_at']) <= cutoff:
            items.append((stamp(b['confirmed_at']), 1, b['timestamp'] + b['timeframe'], b))
    for _, kind, _, item in sorted(items, key=lambda x: x[:3]):
        before_pending, before_history = len(s['pending']), len(s['history'])
        if kind == 0:
            _signal(s, item, p)
            if s['uncertainty']:
                return s
        else:
            try:
                _bar(s, item, p)
            except ValueError as exc:
                s['uncertainty'] = [str(exc)]
                return s
        # Preserve IDs and market chronology; discovery is a separate clock.
        for event in s['pending'][before_pending:] + s['history'][before_history:]:
            event.setdefault('bar_at', item['timestamp'])
            event.setdefault('confirmed_at', event['at'])
            event.setdefault('detected_at', item.get('first_seen') or now)
            if kind == 0:
                event.setdefault('source_event_id', item['event_id'])
    return s


def acknowledge(state, event_ids, *, confirmed_at=None):
    s = deepcopy(state)
    acknowledged = set(event_ids)
    if confirmed_at:
        ledger = s.setdefault('delivered', [])
        known = {e['id'] for e in ledger}
        for event in s['pending']:
            if event['id'] in acknowledged and event['id'] not in known:
                ledger.append(dict(event, receipt_confirmed_at=confirmed_at,
                    delivered_at=getattr(event_ids, 'delivered_at', None),
                    packet_id=getattr(event_ids, 'packet_id', None)))
    s['pending'] = [e for e in s['pending'] if e['id'] not in acknowledged]
    return s
