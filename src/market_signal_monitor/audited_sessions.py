"""Audited native CBOE:VIX / ICEEUR:BRN1! label grids, not guessed 24/7 hours.

Source: native TradingView symbol_resolved metadata and all four native timeframes
captured 2026-11-10, GitHub run 42803875938, artifact 13757690821.
Artifact SHA256: 14b529cbdfb058353116e5a1a98bf9c6f3049dbda47219b16249d211c6033adc.
VIX uses the provider's inclusive-minute boundaries (08:26/15:16 Chicago),
not the cash-equity calendar. Brent retains dated US/UK DST divergence and
explicit holidays/early closes. Scope is bounded to the audited 2026 calendar;
a later date fails closed until refreshed, never silently assumes no holidays.
No source values, signal formulas, durable checkpoints or delivery IDs change.
"""
from functools import lru_cache
import re
import pandas as pd

SPECS = {'BRENT': {'corrections': {'20260525': '2300F-1830',
                           '20260619': '0100-1830',
                           '20260703': '0100-1830',
                           '20260907': '2300F-1830',
                           '20261126': '0000-1830',
                           '20261127': '0000-2000',
                           '20261224': '0000-1900',
                           '20261231': '0000-2000'},
           'effective': [('20260101', '0100-2300|2300F-2300:2'),
                         ('20260309', '0000-2200|2200F-2200:2'),
                         ('20260330', '0100-2300|2300F-2300:2'),
                         ('20261026', '0000-2200|2200F-2200:2'),
                         ('20261102', '0100-2300|2300F-2300:2')],
           'holidays': ['20260101', '20260403', '20261225'],
           'timezone': 'Europe/London',
           'valid_from': '2026-01-01',
           'valid_through': '2026-12-31'},
 'VIX': {'corrections': {'20261127': '0215-0826,0830-1216', '20261224': '0215-0826,0830-1216'},
         'effective': [('20260101', '0215-0826,0830-1516')],
         'holidays': ['20260101',
                      '20260119',
                      '20260216',
                      '20260403',
                      '20260525',
                      '20260619',
                      '20260703',
                      '20260907',
                      '20261126',
                      '20261225'],
         'timezone': 'America/Chicago',
         'valid_from': '2026-01-01',
         'valid_through': '2026-12-31'}}


def native_policy(instrument):
    pair = (instrument.get('exchange'), instrument.get('symbol'))
    feed = {('CBOE', 'VIX'): 'VIX', ('ICEEUR', 'BRN1!'): 'BRENT'}.get(pair)
    if feed is None:
        return None
    return {'type': 'audited_native', 'feed': feed,
            'timezone': SPECS[feed]['timezone'],
            'display_timezone': 'America/New_York' if feed == 'VIX' else 'Europe/London',
            'id': feed.lower() + '-native-session-2026-v1'}


def _wall(day, value, zone):
    match = re.fullmatch(r'(\d{2})(\d{2})(F)?', value)
    if not match:
        raise ValueError('Unsupported audited session clock')
    hh, mm, previous = match.groups()
    date = pd.Timestamp(day) - (pd.Timedelta(days=1) if previous else pd.Timedelta(0))
    return (date + pd.Timedelta(hours=int(hh), minutes=int(mm))).tz_localize(zone).tz_convert('UTC')


def _segments(day, spec):
    key = day.strftime('%Y%m%d')
    if day.weekday() >= 5 or key in spec['holidays']:
        return []
    pattern = spec['effective'][0][1]
    for effective, value in spec['effective']:
        if key >= effective:
            pattern = value
    pattern = spec['corrections'].get(key, pattern)
    if pattern == 'dayoff':
        return []
    # TradingView weekdays: Sunday=1, Monday=2. An explicit day section
    # overrides the default section; it does not add a second session.
    default, selected = None, None
    for part in pattern.split('|'):
        if ':' in part:
            times, days = part.rsplit(':', 1)
            if str((day.weekday()+1) % 7+1) in days:
                selected = times
        else:
            default = part
    times = selected if selected is not None else default
    if times is None:
        return []
    segments = []
    for item in times.split(','):
        op, cl = item.split('-', 1)
        opening, closing = _wall(day, op, spec['timezone']), _wall(day, cl, spec['timezone'])
        if closing <= opening:
            raise ValueError('Non-positive audited session')
        segments.append((opening, closing))
    return segments


@lru_cache(maxsize=128)
def _grid(feed, timeframe, start, end):
    spec = SPECS[feed]
    lo = max(pd.Timestamp(start), pd.Timestamp(spec['valid_from']))
    hi = min(pd.Timestamp(end), pd.Timestamp(spec['valid_through']))
    if lo > hi:
        return ()
    durations = {'30m': pd.Timedelta(minutes=30), '4H': pd.Timedelta(hours=4)}
    if timeframe not in {*durations, '1D', '1W'}:
        raise ValueError('Unsupported audited timeframe')
    sessions = []
    for day in pd.date_range(lo, hi, freq='D'):
        segments = _segments(day, spec)
        if segments:
            sessions.append((day, segments))
    if timeframe == '1W':
        weeks = {}
        for day, segments in sessions:
            monday = (day - pd.Timedelta(days=day.weekday())).date()
            weeks.setdefault(monday, [segments[0][0], segments[-1][1]])[1] = segments[-1][1]
        # Do not certify a weekly close from an incomplete audited week.
        return tuple(tuple(value) for monday, value in weeks.items()
                     if pd.Timestamp(monday) >= pd.Timestamp(spec['valid_from'])
                     and pd.Timestamp(monday) + pd.Timedelta(days=4) <= pd.Timestamp(spec['valid_through']))
    result = []
    for _, segments in sessions:
        if timeframe == '1D':
            result.append((segments[0][0], segments[-1][1]))
            continue
        duration = durations[timeframe]
        for opening, closing in segments:
            cursor = opening
            while cursor < closing:
                result.append((cursor, min(cursor+duration, closing)))
                cursor += duration
    return tuple(result)


def native_grid(policy, timeframe, start, end):
    # Widen to complete weeks so partial range selection cannot invent an
    # early weekly close or shift the weekly opening to the query start.
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    start -= pd.Timedelta(days=start.weekday()+7)
    # Sunday Brent labels belong to the following trading week.
    end += pd.Timedelta(days=13-end.weekday())
    return _grid(policy['feed'], timeframe, start.date().isoformat(), end.date().isoformat())


def native_confirmation_times(bars, policy, timeframe, strict_since):
    local = bars.index.tz_convert(policy['timezone'])
    grid = dict(native_grid(policy, timeframe, local.min().date(), local.max().date()))
    result = []
    for pos, stamp in enumerate(bars.index):
        close = grid.get(stamp, pd.NaT)
        if pd.isna(close):
            if stamp >= strict_since:
                raise ValueError(f'Unknown/out-of-scope audited native bar anchor: {stamp}')
            # Old warmup labels retain actual successor evidence only. This
            # does not certify coverage or the latest bar outside the audit.
            close = bars.index[pos+1] if pos+1 < len(bars) else pd.NaT
        result.append(close)
    return result


def native_expected(policy, timeframe, start_exclusive, now):
    spec = SPECS[policy['feed']]
    start_exclusive, now = pd.Timestamp(start_exclusive), pd.Timestamp(now)
    if start_exclusive.tzinfo is None or now.tzinfo is None:
        raise ValueError('Audited sessions require aware timestamps')
    start_day = start_exclusive.tz_convert(policy['timezone']).date()
    now_day = now.tz_convert(policy['timezone']).date()
    if (start_day < pd.Timestamp(spec['valid_from']).date()
            or now_day > pd.Timestamp(spec['valid_through']).date()):
        return None
    grid = native_grid(policy, timeframe, start_day, now_day)
    return sorted({op for op, cl in grid if op > start_exclusive and cl <= now})
