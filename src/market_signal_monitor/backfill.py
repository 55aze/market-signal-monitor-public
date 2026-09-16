"""Fixed-window backfill with durable, immutable per-stream delivery manifests.

Coverage describes returned bars, not a verified exchange calendar. Never writes
Continuous Through: backfill is not evidence of gap-free live processing.
"""
import json
import os
from datetime import datetime, timezone
import pandas as pd
from .events import extract_events, indicator_version
from .indicator import calculate
from .notion import rich
from .confirmation import confirmation_times, has_close_policy, policy_id


def utcnow():
    return pd.Timestamp(datetime.now(timezone.utc))


def _backfill_bars(config, timeframe):
    value = config.get('backfill_bars', config.get('bars', 5000))
    if isinstance(value, dict):
        value = value.get(timeframe, value.get('default', config.get('bars', 5000)))
    value = int(value)
    if value < 1:
        raise ValueError('backfill_bars must be >= 1')
    return value


class StatusStore:
    def __init__(self, client, source):
        self.client, self.source = client, source

    def preflight(self):
        actual = self.client.request('GET', f'data_sources/{self.source}')['properties']
        schema = {'Stream': 'title', 'Backfill State': 'rich_text',
                  'Backfill Status': 'select', 'Scan Status': 'select', 'Error': 'rich_text',
                  'Unresolved Gap': 'rich_text', 'Run URL': 'url', 'Current Structure': 'rich_text'}
        schema.update({name: 'date' for name in ('Last Attempt', 'Last Success',
            'Backfill Window Start', 'Backfill Window End', 'Coverage Start', 'Coverage End',
            'Continuous Through')})
        if any(actual.get(k, {}).get('type') != v for k, v in schema.items()):
            raise ValueError('Scan Status schema mismatch or integration access missing')

    def _row(self, stream):
        rows = list(self.client.query(self.source, {'property': 'Stream', 'title': {'equals': stream}}))
        if len(rows) != 1:
            raise ValueError(f'Expected exactly one Scan Status row for {stream}')
        return rows[0]

    def load(self, stream):
        row = self._row(stream)
        raw = ''.join(x.get('plain_text', x.get('text', {}).get('content', ''))
                      for x in row['properties']['Backfill State']['rich_text'])
        return row['id'], json.loads(raw) if raw else None

    def backfill_status(self, stream):
        row = self._row(stream)
        value = row['properties']['Backfill Status'].get('select')
        return value.get('name') if value else None

    def load_live(self, stream):
        row = self._row(stream)
        value = row['properties']['Continuous Through'].get('date')
        checkpoint = pd.Timestamp(value['start']) if value else None
        return row['id'], checkpoint

    @staticmethod
    def _run_url_props():
        if os.environ.get('GITHUB_RUN_ID'):
            return {'Run URL': {'url':
                f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}"}}
        return {}

    def live_attempt(self, page, at):
        props = {'Last Attempt': {'date': {'start': pd.Timestamp(at).isoformat()}},
                 **self._run_url_props()}
        self.client.request('PATCH', f'pages/{page}', {'properties': props})

    def live_success(self, page, through, at, structure=None):
        props = {'Scan Status': {'select': {'name': 'Success'}},
                 'Last Attempt': {'date': {'start': pd.Timestamp(at).isoformat()}},
                 'Last Success': {'date': {'start': pd.Timestamp(at).isoformat()}},
                 'Continuous Through': {'date': {'start': pd.Timestamp(through).isoformat()}},
                 'Error': {'rich_text': []}, 'Unresolved Gap': {'rich_text': []},
                 **self._run_url_props()}
        if structure is not None:
            raw = json.dumps(structure, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            props['Current Structure'] = {'rich_text': rich(raw)}
        self.client.request('PATCH', f'pages/{page}', {'properties': props})

    def live_failure(self, page, status, error, at):
        if status not in ('Fetch Failed', 'Write Failed', 'Insufficient History'):
            raise ValueError('Invalid live status')
        props = {'Scan Status': {'select': {'name': status}},
                 'Last Attempt': {'date': {'start': pd.Timestamp(at).isoformat()}},
                 'Error': {'rich_text': rich(error)},
                 'Unresolved Gap': {'rich_text': rich(error)},
                 **self._run_url_props()}
        self.client.request('PATCH', f'pages/{page}', {'properties': props})

    def save(self, page, state, status='Partial', error=''):
        raw = json.dumps(state, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
        if len(raw.encode()) > 170000 or len(rich(raw)) > 90:
            raise ValueError('Backfill manifest too large; no events written')
        props = {'Backfill State': {'rich_text': rich(raw)},
                 'Backfill Status': {'select': {'name': status}},
                 'Last Attempt': {'date': {'start': utcnow().isoformat()}},
                 'Error': {'rich_text': rich(error)},
                 'Backfill Window Start': {'date': {'start': state['start']}},
                 'Backfill Window End': {'date': {'start': state['end']}}}
        props.update(self._run_url_props())
        if state.get('manifest') is not None:
            props['Unresolved Gap'] = {'rich_text': rich(state['coverage_note'])}
        if status in ('Complete', 'Insufficient History'):
            props['Last Success'] = {'date': {'start': utcnow().isoformat()}}
            for prop, key in (('Coverage Start', 'coverage_start'), ('Coverage End', 'coverage_end')):
                props[prop] = {'date': {'start': state[key]} if state.get(key) else None}
        # Scan Status stays Paused: this operation does not activate live monitoring.
        self.client.request('PATCH', f'pages/{page}', {'properties': props})


def run_backfill(config, *, client, fetcher, selected, limit=None, now=None, store=None):
    from .scanner import _warmup_for, attach_curve, curve_snapshots
    from .history import POLICY, dependency_masks, history_evidence
    end = pd.Timestamp(now) if now is not None else utcnow()
    if end.tzinfo is None or end > utcnow():
        raise ValueError('Backfill end must be timezone-aware and not future')
    if not selected:
        raise ValueError('Phase 3 requires explicit --instruments (start with QQQ)')
    unknown = set(selected) - {x['id'] for x in config['instruments']}
    if unknown:
        raise ValueError(f'Unknown instruments: {sorted(unknown)}')
    client.preflight()
    store = store or StatusStore(client, config['notion']['status_data_source'])
    store.preflight()
    report = dict(mode='backfill', run_at=end.isoformat(), status='running',
                  streams=[], events=[], created=0, existing=0, errors=[], warnings=[])
    version = indicator_version(config['pine_sha256'])
    pending, frames = [], {}
    for instrument in config['instruments']:
        if instrument['id'] not in selected:
            continue
        for tf in instrument.get('timeframes', config['timeframes']):
            key = f"{instrument['id']}:{tf}"
            page, state = None, None
            try:
                months = config['backfill_months'][tf]
                cap = limit if limit is not None else config['backfill_limits'][tf]
                configured_warmup = _warmup_for(config, tf)
                dependency_policy = config.get("history_policy") == POLICY
                warmup = 0 if dependency_policy else configured_warmup
                if months < 1 or not 1 <= cap <= 10:
                    raise ValueError('Invalid backfill months or per-direction cap (1..10)')
                identity = dict(version=version, instrument=instrument, timeframe=tf,
                                months=months, cap=cap, warmup=configured_warmup, schema=1)
                page, state = store.load(key)
                persisted_status = getattr(store, 'backfill_status', lambda _key: None)(key)
                if state is None:
                    state = dict(plan=identity, start=(end - pd.DateOffset(months=months)).isoformat(),
                                 end=end.isoformat(), manifest=None)
                confirmation_migration = False
                if state['plan'] != identity:
                    # Confirmation metadata changes do not change provider identity.
                    # All other plan fields must match exactly; refreshed selections
                    # still pass the old-event subset safeguard below.
                    old_plan = {**state['plan'], 'instrument': {k: v for k, v in
                        state['plan']['instrument'].items() if k != 'confirmation'}}
                    new_plan = {**identity, 'instrument': {k: v for k, v in
                        identity['instrument'].items() if k != 'confirmation'}}
                    if old_plan != new_plan:
                        raise ValueError('Backfill plan changed; explicit migration required, existing plan preserved')
                    confirmation_migration = True
                store.save(page, state)
                calendar = has_close_policy(instrument)
                confirmation_policy = policy_id(instrument)
                refresh = (confirmation_migration or (calendar and state.get('confirmation_policy') != confirmation_policy)
                           or (dependency_policy and state.get('history_policy') != POLICY))
                retry_insufficient = (persisted_status == 'Insufficient History'
                                      and state.get('manifest') is not None
                                      and state.get('sufficient') is False)
                if state['manifest'] is None or refresh or retry_insufficient:
                    bars = fetcher(instrument, tf, _backfill_bars(config, tf))
                    fetch_now = utcnow()
                    if bars.empty or bars.index.tz is None or not bars.index.is_monotonic_increasing or not bars.index.is_unique:
                        raise ValueError('Invalid provider timestamps')
                    if bars.index[-1] > fetch_now:
                        raise ValueError('Provider returned a future bar timestamp')
                    start, cutoff = pd.Timestamp(state['start']), pd.Timestamp(state['end'])
                    # Preserve full prehistory for calculation; bound emission before applying caps.
                    confirmations = confirmation_times(
                        bars, instrument, tf, strict_since=start
                    ) if calendar else list(bars.index[1:]) + [pd.NaT]
                    calculated = calculate(bars)
                    readiness = dependency_masks(calculated) if dependency_policy else None
                    eligible = [i for i in range(warmup, len(bars))
                                if bars.index[i] >= start and pd.notna(confirmations[i]) and confirmations[i] <= cutoff
                                and (readiness is None or readiness.iloc[i].all())]
                    sufficient = (bool(eligible)
                                  and len(bars) > warmup + (0 if calendar else 1)
                                  and bars.index[warmup] <= start)
                    if dependency_policy:
                        # Full-window coverage requires dependencies at the first returned
                        # bar in the requested window, not merely some later eligible bar.
                        window_rows = [i for i in range(len(bars)) if bars.index[i] >= start
                                       and pd.notna(confirmations[i]) and confirmations[i] <= cutoff]
                        sufficient = bool(bars.index[0] <= start and window_rows
                                          and readiness.iloc[window_rows].all(axis=None))
                    events = []
                    if len(bars) > warmup + (0 if calendar else 1):
                        events = extract_events(bars, calculated, instrument, tf, version, fetch_now,
                            backfill=True, limit=cap, warmup=warmup, window_start=start, window_end=cutoff,
                            confirmations=confirmations if calendar else None, readiness=readiness)
                    for event in events:
                        event['value_unit'] = 'Yield %' if instrument.get('unit') == 'percent_yield' else 'Price'
                    if (refresh or retry_insufficient) and state['manifest'] is not None:
                        old = {e['event_id']: e for e in state['manifest']}
                        new = {e['event_id']: e for e in events}
                        if not set(old) <= set(new):
                            raise ValueError('History refresh changes prior capped selection; reviewed reconciliation required; existing events preserved')
                        events = [old.get(e['event_id'], e) for e in events]
                    state.update(plan=identity, history_policy=POLICY if dependency_policy else "legacy-warmup",
                        confirmation_policy=confirmation_policy,
                        manifest=events, sufficient=sufficient,
                        coverage_start=bars.index[eligible[0]].isoformat() if eligible else None,
                        coverage_end=bars.index[eligible[-1]].isoformat() if eligible else None,
                        coverage_confirmed_at=confirmations[eligible[-1]].isoformat() if eligible else None,
                        coverage_note=('' if sufficient else ('Requested window lacks complete observed signal dependencies. '
                             if dependency_policy else 'Requested start precedes usable history after warmup. '))
                            + 'Returned bars only; exchange-calendar gaps and end freshness are not verified.')
                    if dependency_policy:
                        confirmed = [i for i, close in enumerate(confirmations)
                                     if pd.notna(close) and close <= cutoff]
                        if confirmed:
                            state['history'] = history_evidence(bars, calculated, readiness,
                                bars.index[confirmed[-1]], _backfill_bars(config, tf))
                    frames[instrument['id'], tf] = bars
                pending.append((key, page, state))
            except (ValueError, RuntimeError, OSError) as exc:
                report['errors'].append({'stream': key, 'error': str(exc)})
                if page and state and state.get('plan') == identity:
                    try:
                        store.save(page, state, error=str(exc))
                    except (ValueError, RuntimeError, OSError):
                        pass  # Original failure is retained in the run artifact.
    curves = curve_snapshots(frames)
    for key, page, state in pending:
        try:
            # Freeze curve context and event payload BEFORE the first event create.
            for event in state['manifest']:
                if 'curve_status' not in event:
                    attach_curve(event, curves)
            store.save(page, state)
            for event in state['manifest']:
                created = client.append(event)
                report['created' if created else 'existing'] += 1
                report['events'].append(event)
            status = 'Complete' if state['sufficient'] else 'Insufficient History'
            store.save(page, state, status=status)
            report['streams'].append({'stream': key, 'status': status,
                'window_start': state['start'], 'window_end': state['end'],
                'events': len(state['manifest']), 'coverage_note': state['coverage_note']})
            if 'history' in state:
                report['streams'][-1]['history'] = state['history']
            report['streams'][-1]['confirmation_policy'] = state['confirmation_policy']
            report['streams'][-1]['coverage_confirmed_at'] = state.get('coverage_confirmed_at')
            if not state['sufficient']:
                report['warnings'].append({'stream': key, 'warning': state['coverage_note']})
        except (ValueError, RuntimeError, OSError) as exc:
            report['errors'].append({'stream': key, 'error': str(exc)})
            # Record failure without claiming delivery completion.
            try:
                store.save(page, state, error=str(exc))
            except (ValueError, RuntimeError, OSError):
                pass
            # No further event writes after an ambiguous response.
            break
    report['status'] = 'partial_failure' if report['errors'] else ('completed_with_warnings' if report['warnings'] else 'completed')
    return report
