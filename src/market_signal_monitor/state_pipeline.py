"""Native-bar adapter: origin coverage is independent of raw scanner checkpoints."""
from copy import deepcopy
import pandas as pd
from .snapshot import finite, snapshot
from .state_engine import advance, policy, TF, identity


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
