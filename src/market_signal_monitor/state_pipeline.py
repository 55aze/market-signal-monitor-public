"""Native-bar adapter: origin coverage is independent of raw scanner checkpoints."""
from copy import deepcopy
import pandas as pd
from .snapshot import finite, snapshot
from .state_engine import advance, policy, TF, identity, stamp


def _append_raw_signals(state, events, now):
    """Keep every live signal in the durable report outbox, including 30m noise.

    Raw delivery is independent of lifecycle replay: an unavailable sibling
    stream may freeze setup transitions without hiding a confirmed signal.
    Existing ``seen`` IDs form the cutover baseline for pre-existing states.
    """
    seen = set(state.get('seen', ())) | set(state.get('raw_seen', ()))
    for event in events:
        event_id = event.get('event_id')
        confirmed = event.get('confirmation_at') or event.get('next_bar_at')
        if (event.get('backfill') or event.get('ticker') != state['ticker'] or
                event.get('timeframe') not in TF or event.get('signal') not in {'Bottom', 'Sell'} or
                not event_id or not confirmed or event_id in seen or stamp(confirmed) > stamp(now) or
                (state.get('signal_cutoff') and stamp(confirmed) <= stamp(state['signal_cutoff']))):
            continue
        state['pending'].append({
            'id': identity('raw-signal', event_id), 'kind': 'RAW_SIGNAL',
            'source_event_id': event_id, 'ticker': state['ticker'],
            'signal': event['signal'], 'origin_tf': event['timeframe'],
            'at': confirmed, 'bar_at': event['timestamp'],
            'confirmed_at': confirmed, 'detected_at': event.get('first_seen'),
            'price': event.get('price'), 'value_unit': event.get('value_unit', 'Price'),
            'market_timezone': event.get('market_timezone')})
        state.setdefault('raw_seen', []).append(event_id)
        seen.add(event_id)


def observation_batch(bars, calculated, instrument, timeframe, confirmations, now):
    result = []
    for i, (at, row) in enumerate(bars.iterrows()):
        confirmed = (confirmations[i] if confirmations is not None else
                     bars.index[i + 1] if i + 1 < len(bars) else pd.NaT)
        if pd.isna(confirmed) or confirmed > now:
            continue
        calc = calculated.iloc[i]
        result.append(dict(timeframe=timeframe, timestamp=at.isoformat(),
            confirmed_at=confirmed.isoformat(), close=finite(row.close), low=finite(row.low),
            high=finite(row.high), blue_lower=finite(calc.get('blueLowerBand')),
            blue_upper=finite(calc.get('blueUpperBand')), snapshot=snapshot(row.close, calc),
            value_unit='Yield %' if instrument.get('unit') == 'percent_yield' else 'Price'))
    return result


def _process_ticker(state, batches, events, now, rules=None):
    s = deepcopy(state)
    _append_raw_signals(s, events, now)
    origin = s['origin_tf']
    for tf, batch in batches.items():
        if batch.get('coverage'):
            s.setdefault('coverage', {})[tf] = deepcopy(batch['coverage'])
    if 'Late dominant signal requires explicit chronological replay' in s['uncertainty']:
        return s
    # A failure in any stream can hide a dominant opposite signal. Freeze ticker
    # transitions conservatively; healthy scanner delivery may still continue.
    errors = s.setdefault('stream_errors', {})
    for tf, b in batches.items():
        if b.get('error'):
            errors[tf] = True
        elif b.get('bars'):
            errors.pop(tf, None)
    failures = list(errors)
    if failures:
        s['uncertainty'] = ['Unavailable stream(s): ' + ', '.join(sorted(failures))]
        return s
    for tf, batch in batches.items():
        rows = batch.get('bars', [])
        if rows:
            last = rows[-1]
            s['structures'][tf] = {k: last[k] for k in ('timestamp', 'confirmed_at', 'snapshot', 'value_unit')}
    if origin in TF and s['last_bar']:
        rows = batches.get(origin, {}).get('bars', [])
        if rows and pd.Timestamp(s['last_bar']) not in {pd.Timestamp(b['timestamp']) for b in rows}:
            s['uncertainty'] = ['Origin checkpoint absent from fetched bars; replay gap unknown']
            return s
    # A recovered fetch is independent of whether the origin timeframe is due.
    s['uncertainty'] = [reason for reason in s['uncertainty']
                        if not (reason.startswith('Unavailable stream(s): ') and
                            all(batches.get(tf, {}).get('bars') and not batches[tf].get('error')
                                for tf in reason.split(': ', 1)[1].split(', ')))]
    if s['uncertainty'] and not batches.get(origin, {}).get('bars'):
        return s
    s['uncertainty'] = []
    bars = [b for batch in batches.values() for b in batch.get('bars', [])]
    s = advance(s, bars, events, now, policy(rules))
    # Successful lifecycle replay now remembers the signal in ``seen``.
    s['raw_seen'] = [event_id for event_id in s.get('raw_seen', ()) if event_id not in s['seen']]
    # Parent context is descriptive and never silently changes the setup stage.
    parent = next((tf for tf in TF if TF[tf] > TF.get(s['origin_tf'], 99)), None)
    if parent in s['structures']:
        a = s['structures'][parent]['snapshot']['alignment']
        s['parent_regime'] = {'Bullish':'Bull', 'Bearish':'Bear'}.get(a, a)
    return s


def process_ticker(state, batches, events, now, rules=None):
    result = _process_ticker(state, batches, events, now, rules)
    if result['uncertainty'] != state['uncertainty']:
        event = dict(id=identity(state['ticker'], now, result['uncertainty']),
                     kind='DATA_GAP' if result['uncertainty'] else 'DATA_RECOVERED',
                     ticker=state['ticker'], at=now, detected_at=now, bar_at=None, confirmed_at=None,
                     reason='; '.join(result['uncertainty']) or 'Data coverage restored')
        result['pending'].append(event)
    return result
