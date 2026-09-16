"""Expected-bar reconciliation for live scanner freshness and gap detection."""
import pandas as pd

from .confirmation import policy_for, _overnight_sessions, _fixed_week_bounds


_DURATIONS = {
    "30m": pd.Timedelta(minutes=30),
    "1H": pd.Timedelta(hours=1),
    "4H": pd.Timedelta(hours=4),
    "1D": pd.Timedelta(days=1),
    "1W": pd.Timedelta(days=7),
}


def _duration(timeframe):
    try:
        return _DURATIONS[timeframe]
    except KeyError as exc:
        raise ValueError("Unsupported freshness timeframe") from exc


def _utc(value):
    value = pd.Timestamp(value)
    if value.tzinfo is None:
        raise ValueError("Freshness timestamps must be timezone-aware")
    return value.tz_convert("UTC")


def _calendar_expected(policy, timeframe, start_exclusive, now):
    import pandas_market_calendars as mcal
    start_exclusive, now = _utc(start_exclusive), _utc(now)
    timezone = policy["timezone"]
    local_start = start_exclusive.tz_convert(timezone)
    local_now = now.tz_convert(timezone)
    schedule = mcal.get_calendar(policy["calendar"]).schedule(
        start_date=(local_start - pd.Timedelta(days=8)).date(),
        end_date=(local_now + pd.Timedelta(days=8)).date())
    if schedule.empty:
        return []
    expected = []

    def add(open_at, close_at):
        open_at, close_at = _utc(open_at), _utc(close_at)
        if open_at > start_exclusive and close_at <= now:
            expected.append(open_at)

    if timeframe == "1W":
        weeks = {}
        for day, row in schedule.iterrows():
            day = pd.Timestamp(day)
            week = (day - pd.Timedelta(days=day.weekday())).date()
            if week not in weeks:
                weeks[week] = [row.market_open, row.market_close]
            else:
                weeks[week][1] = row.market_close
        for opening, closing in weeks.values():
            add(opening, closing)
        return sorted(set(expected))

    duration = _duration(timeframe)
    for _, row in schedule.iterrows():
        opening, session_close = row.market_open, row.market_close
        if timeframe == "1D":
            add(opening, session_close)
            continue
        if timeframe == "4H" and policy["calendar"] == "SSE":
            add(opening, session_close)
            continue
        break_start = row.get("break_start", pd.NaT)
        break_end = row.get("break_end", pd.NaT)
        if pd.notna(break_start) and pd.notna(break_end):
            segments = ((opening, break_start), (break_end, session_close))
        else:
            segments = ((opening, session_close),)
        for segment_open, segment_close in segments:
            cursor = segment_open
            while cursor < segment_close:
                add(cursor, min(cursor + duration, segment_close))
                cursor += duration
        if timeframe in policy.get("close_label_timeframes", ()):
            add(session_close, session_close)
    return sorted(set(expected))


def _overnight_expected(policy, timeframe, start_exclusive, now):
    start_exclusive, now = _utc(start_exclusive), _utc(now)
    timezone = policy["timezone"]
    local_start = start_exclusive.tz_convert(timezone)
    local_now = now.tz_convert(timezone)
    sessions = _overnight_sessions(
        policy,
        (local_start - pd.Timedelta(days=8)).date(),
        (local_now + pd.Timedelta(days=8)).date(),
        timeframe)
    expected = []

    def add(open_at, close_at):
        open_at, close_at = _utc(open_at), _utc(close_at)
        if open_at > start_exclusive and close_at <= now:
            expected.append(open_at)

    if timeframe == "1W":
        weeks = {}
        for day, opening, closing in sessions:
            monday = day - pd.Timedelta(days=pd.Timestamp(day).weekday())
            weeks.setdefault(monday, [opening, closing])[1] = closing
        for opening, closing in weeks.values():
            add(opening, closing)
        return sorted(set(expected))

    duration = _duration(timeframe)
    for _, opening, closing in sessions:
        if timeframe == "1D":
            add(opening, closing)
            continue
        cursor = opening
        while cursor < closing:
            add(cursor, min(cursor + duration, closing))
            cursor += duration
    return sorted(set(expected))


def _fixed_week_expected(policy, timeframe, start_exclusive, now):
    start_exclusive, now = _utc(start_exclusive), _utc(now)
    duration = _duration(timeframe)
    probe = start_exclusive - pd.Timedelta(days=7)
    opening, closing = _fixed_week_bounds(policy, probe, timeframe)
    while closing <= start_exclusive:
        opening += pd.Timedelta(days=7)
        closing += pd.Timedelta(days=7)
    expected = []
    while opening <= now:
        if timeframe == "1W":
            if opening > start_exclusive and closing <= now:
                expected.append(opening)
        elif timeframe == "1D":
            cursor = opening
            while cursor < closing:
                confirmed = min(cursor + pd.Timedelta(days=1), closing)
                if cursor > start_exclusive and confirmed <= now:
                    expected.append(cursor)
                cursor += pd.Timedelta(days=1)
        else:
            cursor = opening
            while cursor < closing:
                confirmed = min(cursor + duration, closing)
                if cursor > start_exclusive and confirmed <= now:
                    expected.append(cursor)
                cursor += duration
        opening += pd.Timedelta(days=7)
        closing += pd.Timedelta(days=7)
    return sorted(set(expected))


def _is_continuous_24_7(instrument, policy):
    return (policy.get("gap_schedule") == "continuous_24_7"
            or instrument.get("gap_schedule") == "continuous_24_7"
            or (instrument.get("exchange") == "BITSTAMP" and instrument.get("symbol") == "BTCUSD"))


def _continuous_expected(instrument, policy, timeframe, start_exclusive, now):
    if not _is_continuous_24_7(instrument, policy):
        return None
    start_exclusive, now = _utc(start_exclusive), _utc(now)
    duration = _duration(timeframe)
    result, cursor = [], start_exclusive + duration
    while cursor + duration <= now:
        result.append(cursor)
        cursor += duration
    return result


def _expected(instrument, timeframe, start_exclusive, now):
    policy = policy_for(instrument)
    if policy is None:
        return None, "no_close_policy"
    if policy["type"] == "exchange_calendar":
        return _calendar_expected(policy, timeframe, start_exclusive, now), "exchange_calendar"
    if policy["type"] == "overnight_calendar":
        return _overnight_expected(policy, timeframe, start_exclusive, now), "overnight_calendar"
    if policy["type"] == "fixed_week":
        return _fixed_week_expected(policy, timeframe, start_exclusive, now), "fixed_week"
    if policy["type"] == "continuous_interval":
        values = _continuous_expected(instrument, policy, timeframe, start_exclusive, now)
        return values, ("continuous_24_7" if values is not None else "unsupported_continuous")
    return None, "unsupported_policy"


def _serialize(values, limit=12):
    values = list(values)
    return [x.isoformat() for x in values[:limit]], len(values) > limit


def scan_due(instrument, timeframe, checkpoint, now):
    """Gate higher timeframes by durable progress, never by a successful fetch alone.

    Unknown calendars keep polling: a guessed close can suppress real signals.
    A missing checkpoint always bootstraps. Failed delivery leaves work due.
    """
    if timeframe == "30m" or checkpoint is None:
        return True
    checkpoint, now = _utc(checkpoint), _utc(now)
    if checkpoint > now:
        raise ValueError("Live checkpoint is in the future")
    expected, _ = _expected(instrument, timeframe, checkpoint, now)
    return expected is None or bool(expected)


def assess_freshness(bars_index, instrument, timeframe, now, latest_processed,
                     previous_through=None, enabled=True):
    now, actual = _utc(now), _utc(latest_processed)
    checkpoint = _utc(previous_through) if previous_through is not None else None
    base = {"status": "unverifiable", "verification": "disabled" if not enabled else None,
            "expected_latest": None, "actual_latest": actual.isoformat(),
            "checkpoint": checkpoint.isoformat() if checkpoint is not None else None,
            "missing_count": 0, "missing_bar_opens": [], "missing_truncated": False}
    if not enabled:
        return base
    start = checkpoint if checkpoint is not None else actual
    expected, verification = _expected(instrument, timeframe, start, now)
    base["verification"] = verification
    if expected is None:
        return base
    expected = [_utc(x) for x in expected]
    base["expected_latest"] = expected[-1].isoformat() if expected else actual.isoformat()
    returned = {_utc(x) for x in bars_index}
    if checkpoint is None:
        missing, interior = expected, []
        tail = missing
    else:
        missing = [stamp for stamp in expected if stamp not in returned]
        interior = [stamp for stamp in missing if stamp <= actual]
        tail = [stamp for stamp in missing if stamp > actual]
    if interior:
        status, relevant = "interior_gap", missing
    elif tail:
        status, relevant = "stale_tail", tail
    else:
        status, relevant = "current", []
    serialized, truncated = _serialize(relevant)
    base.update({"status": status, "missing_count": len(relevant),
                 "missing_bar_opens": serialized, "missing_truncated": truncated})
    return base


def gap_error(freshness):
    if freshness.get("status") != "interior_gap":
        return None
    opens = ", ".join(freshness.get("missing_bar_opens", []))
    suffix = " ..." if freshness.get("missing_truncated") else ""
    return ("Expected confirmed bar gap after durable checkpoint: "
            f"{freshness.get('missing_count', 0)} missing; "
            f"expected_latest={freshness.get('expected_latest')}; "
            f"actual_latest={freshness.get('actual_latest')}; "
            f"missing_opens=[{opens}{suffix}]")
