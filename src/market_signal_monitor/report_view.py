"""Signal-only report selection, independent of lifecycle qualification."""
from datetime import timedelta
from zoneinfo import ZoneInfo
from .state_engine import TF, stamp
from .confirmation import market_timezone


def compact_structure(tf, structure):
    from .report_packet import structure_ladder
    full = structure_ladder(tf, structure)
    ladder = full.split('  ', 1)[-1]
    if ladder == '● > 🟦 > 🟨 > E200':
        return 'bull'
    if ladder == 'E200 > 🟨 > 🟦 > ●':
        return 'bear'
    return ladder.replace('顶', '').replace('底', '')


def watch_context(states, now, instruments):
    cutoff = stamp(now)
    metadata = {key: i for i in instruments for key in (i['id'], i.get('notion_ticker', i['id']))}
    rows = []
    for s in states:
        ticker = s['ticker']
        instrument = metadata.get(ticker)
        zone = (instrument.get("market_timezone") or market_timezone(instrument)) if instrument else None
        signals = {}
        candidates = list(s.get('lineage', []))
        for e in s.get('pending', []) + s.get('delivered', []):
            if e.get('kind') == 'RAW_SIGNAL':
                candidates.append(dict(timeframe=e.get('origin_tf'), signal=e.get('signal'),
                    timestamp=e.get('bar_at'), confirmation_at=e.get('confirmed_at'),
                    event_id=e.get('source_event_id')))
        for e in candidates:
            tf, direction = e.get('timeframe'), e.get('signal')
            at, confirmed = e.get('timestamp'), e.get('confirmation_at')
            if tf not in TF or direction not in {'Bottom', 'Sell'} or not at or not confirmed:
                continue
            if not cutoff - timedelta(days=14) <= stamp(confirmed) <= cutoff:
                continue
            # Same bar/direction is one signal even across indicator versions.
            signals[(tf, direction, stamp(at))] = e
        recent = list(signals.values())
        high = [e for e in recent if TF[e['timeframe']] >= TF['4H']]
        cluster = [e for e in recent if e['timeframe'] == '30m' and e['signal'] == 'Bottom'
                   and stamp(e['confirmation_at']) >= cutoff - timedelta(hours=48)]
        if not high and len(cluster) < 3:
            continue
        anchor = max(high or cluster, key=lambda e: (TF[e['timeframe']], stamp(e['confirmation_at'])))
        anchor_date = stamp(anchor['timestamp']).astimezone(ZoneInfo(zone)).date().isoformat() if zone else None
        cells = {}
        for tf in ('1W', '1D', '4H', '30m'):
            structure = s.get('structures', {}).get(tf, {})
            freshness = s.get('coverage', {}).get(tf, {})
            actual = structure.get('timestamp')
            expected = freshness.get('expected_latest')
            current = (freshness.get('status') == 'current' and actual is not None
                       and expected is not None and stamp(actual) == stamp(expected))
            latest = []
            for direction in ('Bottom', 'Sell'):
                matches = [e for e in recent if e['timeframe'] == tf and e['signal'] == direction]
                if matches:
                    e = max(matches, key=lambda e: stamp(e['confirmation_at']))
                    latest.append({k:e.get(k) for k in ('signal', 'timestamp', 'confirmation_at')})
            latest.sort(key=lambda e: stamp(e['confirmation_at']), reverse=True)
            cells[tf] = dict(structure=compact_structure(tf, structure),
                stream=(instrument['id'] + ':' + tf) if instrument else None,
                bar_at=actual, confirmed_at=structure.get('confirmed_at'),
                freshness='current' if current else 'unverified_or_stale', signals=latest)
        rows.append(dict(ticker=ticker, market_timezone=zone, group_date=anchor_date,
            instrument_id=instrument['id'] if instrument else None,
            provider_symbol=instrument.get('symbol') if instrument else None,
            highest_signal_tf=anchor['timeframe'], anchor_confirmed_at=anchor['confirmation_at'],
            bottom_30m_48h=len(cluster), cells=cells))
    rows.sort(key=lambda r: (r['group_date'] or '', TF[r['highest_signal_tf']],
                             r['anchor_confirmed_at'], r['ticker']), reverse=True)
    return dict(version=1, as_of=now, window_days=14, cluster_hours=48,
        cluster_min_count=3, source='engine-observed dated signals; not imported highest-TF labels',
        rows=rows, row_count=len(rows))
