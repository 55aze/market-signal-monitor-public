"""Deterministic close evidence for explicitly configured TradingView streams."""
import pandas as pd


EXCHANGE_TIMEZONES = {
    "NASDAQ": "America/New_York", "NYSE": "America/New_York",
    "CBOE": "America/New_York", "SP": "America/New_York",
    "HKEX": "Asia/Hong_Kong", "SSE": "Asia/Shanghai", "SZSE": "Asia/Shanghai",
}

_US_INDEX_IDS = {"SPX", "SOX", "RUT"}
_TREASURY_YIELD_IDS = {"US02Y", "US05Y", "US10Y", "US30Y"}


def policy_for(instrument):
    policy = instrument.get("confirmation")
    if instrument.get("id") in _TREASURY_YIELD_IDS:
        # Real TVC bars use different anchors by timeframe. The September audit
        # showed 30m at 20:00->17:30 New York and 4H/1D/1W at 19:00 anchors.
        # TVC may omit an illiquid prefix at a session reopen while retaining
        # later aligned bars; freshness handles that prefix separately.
        return {"type": "overnight_calendar", "calendar": "NYSE",
                "timezone": "America/New_York",
                "session_open_by_tf": {"30m": "20:00", "4H": "19:00", "1D": "19:00", "1W": "19:00"},
                "session_close_by_tf": {"30m": "17:30", "4H": "19:00", "1D": "19:00", "1W": "19:00"},
                "optional_session_open_prefix": ["30m", "4H"],
                "id": "tvc-us-yield-session-v2"}
    if instrument.get("id") == "JPYUSD":
        return {"type": "fixed_week", "timezone": "UTC",
                "week_open_weekday": 6, "week_open": "22:00",
                "week_close_weekday": 4, "week_close": "22:00",
                "week_close_by_tf": {"30m": "21:30"},
                "id": "fx-idc-24x5-provider-grid-v2"}
    if instrument.get("id") in _US_INDEX_IDS:
        return {"type": "exchange_calendar", "calendar": "NYSE",
                "timezone": "America/New_York", "close_label_timeframes": ["30m"],
                "id": "us-index-rth-calendar-v2"}
    if policy:
        return policy
    if ((instrument["exchange"], instrument["symbol"]) == ("NASDAQ", "QQQ")
            and not instrument.get("extended_session")):
        return {"type": "exchange_calendar", "calendar": "NASDAQ",
                "timezone": "America/New_York", "id": "qqq-nasdaq-calendar-v2"}
    if instrument.get("exchange") == "SSE" and not instrument.get("extended_session"):
        return {"type": "exchange_calendar", "calendar": "SSE",
                "timezone": "Asia/Shanghai", "id": "sse-rth-calendar-v2"}
    return None


def has_close_policy(instrument):
    return policy_for(instrument) is not None


def policy_id(instrument):
    policy = policy_for(instrument)
    return policy["id"] if policy else "next-bar"


def market_timezone(instrument):
    policy = policy_for(instrument) or {}
    return (instrument.get("market_timezone") or policy.get("timezone")
            or EXCHANGE_TIMEZONES.get(instrument.get("exchange")) or "UTC")


def _duration(timeframe):
    durations = {"30m": pd.Timedelta(minutes=30), "1H": pd.Timedelta(hours=1),
                 "4H": pd.Timedelta(hours=4), "1D": pd.Timedelta(days=1),
                 "1W": pd.Timedelta(days=7)}
    if timeframe not in durations:
        raise ValueError("Unsupported confirmation timeframe")
    return durations[timeframe]


def _wall(day, hhmm, timezone):
    hour, minute = map(int, hhmm.split(":"))
    return pd.Timestamp(day).tz_localize(timezone) + pd.Timedelta(hours=hour, minutes=minute)


def _calendar_schedule(policy, start, end):
    import pandas_market_calendars as mcal
    return mcal.get_calendar(policy["calendar"]).schedule(start_date=start, end_date=end)


def _calendar_times(bars, policy, timeframe, strict_since):
    local = bars.index.tz_convert(policy["timezone"])
    start = local.min().normalize() - pd.Timedelta(days=7)
    end = local.max().normalize() + pd.Timedelta(days=7)
    schedule = _calendar_schedule(policy, start.date(), end.date())
    sessions = {pd.Timestamp(day).date(): row for day, row in schedule.iterrows()}
    weekly = {}
    for day, row in schedule.iterrows():
        date = pd.Timestamp(day)
        week = (date - pd.Timedelta(days=date.weekday())).date()
        if week not in weekly:
            weekly[week] = [row.market_open, row.market_close]
        else:
            weekly[week][1] = row.market_close

    result, duration = [], _duration(timeframe)
    for stamp, wall in zip(bars.index, local):
        if timeframe == "1W":
            week = (wall.normalize() - pd.Timedelta(days=wall.weekday())).date()
            opening, closing = weekly.get(week, (pd.NaT, pd.NaT))
            session = sessions.get(wall.date())
            valid = (pd.notna(closing) and
                     ((wall.weekday() == 0 and wall.hour == 9 and wall.minute == 30)
                      or (session is not None and stamp == session.market_open)))
        elif timeframe == "1D":
            session = sessions.get(wall.date())
            closing = session.market_close if session is not None else pd.NaT
            valid = (session is not None and wall.hour == 9 and wall.minute == 30)
        elif timeframe == "4H" and policy["calendar"] == "SSE":
            session = sessions.get(wall.date())
            closing = session.market_close if session is not None else pd.NaT
            valid = (session is not None and stamp == session.market_open)
        else:
            session = sessions.get(wall.date())
            if session is None:
                valid, closing = False, pd.NaT
            elif (timeframe in policy.get("close_label_timeframes", ())
                  and stamp == session.market_close):
                valid, closing = True, session.market_close
            else:
                opening, session_close = session.market_open, session.market_close
                break_start = session.get("break_start", pd.NaT)
                break_end = session.get("break_end", pd.NaT)
                if pd.notna(break_start) and opening <= stamp < break_start:
                    segment_open, segment_close = opening, break_start
                elif pd.notna(break_end) and break_end <= stamp < session_close:
                    segment_open, segment_close = break_end, session_close
                elif pd.isna(break_start) and opening <= stamp < session_close:
                    segment_open, segment_close = opening, session_close
                else:
                    segment_open = segment_close = pd.NaT
                valid = (pd.notna(segment_open)
                         and (stamp - segment_open) % duration == pd.Timedelta(0))
                closing = min(stamp + duration, segment_close) if valid else pd.NaT
        if not valid and stamp >= strict_since:
            raise ValueError(f"Bar does not match {policy['calendar']} regular-session anchor: {stamp}")
        result.append(closing if valid else pd.NaT)
    return result


def _overnight_sessions(policy, start, end, timeframe):
    """Provider sessions keyed by the following NYSE trading date."""
    tz = policy["timezone"]
    opening_hhmm = policy["session_open_by_tf"][timeframe]
    closing_hhmm = policy["session_close_by_tf"][timeframe]
    schedule = _calendar_schedule(policy, start, end)
    sessions = []
    for day in schedule.index:
        trading_day = pd.Timestamp(day).date()
        opening = _wall(trading_day - pd.Timedelta(days=1), opening_hhmm, tz)
        closing = _wall(trading_day, closing_hhmm, tz)
        sessions.append((trading_day, opening.tz_convert("UTC"), closing.tz_convert("UTC")))
    return sessions


def _overnight_times(bars, policy, timeframe, strict_since):
    local = bars.index.tz_convert(policy["timezone"])
    sessions = _overnight_sessions(policy,
        (local.min() - pd.Timedelta(days=8)).date(),
        (local.max() + pd.Timedelta(days=8)).date(), timeframe)
    duration = _duration(timeframe)
    weekly = {}
    for day, opening, closing in sessions:
        monday = day - pd.Timedelta(days=pd.Timestamp(day).weekday())
        weekly.setdefault(monday, [opening, closing])[1] = closing
    result = []
    for stamp in bars.index:
        closing, valid = pd.NaT, False
        if timeframe == "1W":
            for opening, week_close in weekly.values():
                if stamp == opening:
                    closing, valid = week_close, True
                    break
        else:
            for _, opening, session_close in sessions:
                if timeframe == "1D" and stamp == opening:
                    closing, valid = session_close, True
                    break
                if timeframe not in {"1D", "1W"} and opening <= stamp < session_close:
                    if (stamp - opening) % duration == pd.Timedelta(0):
                        closing, valid = min(stamp + duration, session_close), True
                        break
        if not valid and stamp >= strict_since:
            raise ValueError(f"Bar does not match overnight provider session anchor: {stamp}")
        result.append(closing if valid else pd.NaT)
    return result


def _fixed_week_bounds(policy, stamp, timeframe):
    tz = policy["timezone"]
    wall = stamp.tz_convert(tz)
    open_wd, close_wd = policy["week_open_weekday"], policy["week_close_weekday"]
    days_back = (wall.weekday() - open_wd) % 7
    open_day = (wall.normalize() - pd.Timedelta(days=days_back)).date()
    opening = _wall(open_day, policy["week_open"], tz)
    close_day = open_day + pd.Timedelta(days=(close_wd - open_wd) % 7)
    close_hhmm = policy.get("week_close_by_tf", {}).get(timeframe, policy["week_close"])
    closing = _wall(close_day, close_hhmm, tz)
    if wall < opening:
        open_day = open_day - pd.Timedelta(days=7)
        opening = _wall(open_day, policy["week_open"], tz)
        close_day = open_day + pd.Timedelta(days=(close_wd - open_wd) % 7)
        closing = _wall(close_day, close_hhmm, tz)
    return opening.tz_convert("UTC"), closing.tz_convert("UTC")


def _fixed_week_times(bars, policy, timeframe, strict_since):
    duration = _duration(timeframe)
    result = []
    for stamp in bars.index:
        opening, closing = _fixed_week_bounds(policy, stamp, timeframe)
        if timeframe == "1W":
            valid, confirmed = stamp == opening, closing
        elif timeframe == "1D":
            valid = opening <= stamp < closing and (stamp - opening) % pd.Timedelta(days=1) == pd.Timedelta(0)
            confirmed = min(stamp + pd.Timedelta(days=1), closing) if valid else pd.NaT
        else:
            valid = opening <= stamp < closing and (stamp - opening) % duration == pd.Timedelta(0)
            confirmed = min(stamp + duration, closing) if valid else pd.NaT
        if not valid and stamp >= strict_since:
            raise ValueError(f"Bar does not match fixed-week session anchor: {stamp}")
        result.append(confirmed if valid else pd.NaT)
    return result


def confirmation_times(bars, instrument, timeframe, strict_since=None):
    policy = policy_for(instrument)
    if policy is None:
        return list(bars.index[1:]) + [pd.NaT]
    strict_since = pd.Timestamp(strict_since) if strict_since is not None else bars.index.min()
    if strict_since.tzinfo is None:
        raise ValueError("strict_since must be timezone-aware")
    if policy["type"] == "continuous_interval":
        return [stamp + _duration(timeframe) for stamp in bars.index]
    if policy["type"] == "exchange_calendar":
        return _calendar_times(bars, policy, timeframe, strict_since)
    if policy["type"] == "overnight_calendar":
        return _overnight_times(bars, policy, timeframe, strict_since)
    if policy["type"] == "fixed_week":
        return _fixed_week_times(bars, policy, timeframe, strict_since)
    raise ValueError(f"Unknown confirmation policy: {policy['type']}")
