"""Append-only event identity and conservative completed-bar selection."""
import hashlib
import json
from pathlib import Path
import pandas as pd
from .snapshot import snapshot, finite
from .confirmation import market_timezone


def indicator_version(pine_hash):
    root = Path(__file__).parent
    code = b"\n".join((root / name).read_bytes() for name in ("indicator.py", "pine_ops.py"))
    return f"pine:{pine_hash[:12]}/python:{hashlib.sha256(code).hexdigest()[:12]}"


def extract_events(bars, calculated, instrument, timeframe, version, now,
                   since=None, backfill=False, limit=10, warmup=500,
                   window_start=None, window_end=None, confirmations=None, readiness=None):
    """Timestamp is bar OPEN; next_bar_at is confirmation evidence, not close time.

    Warmup affects emission only. Explicit calendar confirmations can include the latest row.
    Live activation uses bar-open time; an already-open bar at activation is skipped.
    """
    if not bars.index.equals(calculated.index):
        raise ValueError("Calculation and input timestamps differ")
    if warmup < 0 or limit < 1:
        raise ValueError("Invalid warmup or history limit")
    if len(bars) <= warmup + (1 if confirmations is None else 0):
        raise ValueError("Insufficient history after warmup and latest-bar exclusion")
    if readiness is not None and not readiness.index.equals(bars.index):
        raise ValueError("Readiness and input timestamps differ")
    now = pd.Timestamp(now)
    if now.tzinfo is None:
        raise ValueError("now must have a timezone")
    if since is not None:
        since = pd.Timestamp(since)
        if since.tzinfo is None or since > now:
            raise ValueError("since must be timezone-aware and not in the future")
    if not backfill and since is None:
        raise ValueError("Live scan requires a fixed activation timestamp")
    if (window_start is None) != (window_end is None):
        raise ValueError("Both backfill window bounds are required")
    if window_start is not None:
        window_start, window_end = pd.Timestamp(window_start), pd.Timestamp(window_end)
        if (not backfill or window_start.tzinfo is None or window_end.tzinfo is None
                or window_start >= window_end or window_end > now):
            raise ValueError("Invalid backfill window")
    if confirmations is not None and len(confirmations) != len(bars):
        raise ValueError("Confirmation length mismatch")
    source = {"provider": "tvDatafeed_nologin", "exchange": instrument["exchange"],
              "symbol": instrument["symbol"], "session": "extended" if instrument.get("extended_session") else "regular",
              "adjustment": "splits", "timeframe": timeframe}
    events = []
    for column, signal in (("DXDX", "Bottom"), ("DBJGXC", "Sell")):
        matches = []
        for i in range(warmup, len(bars) if confirmations is not None else len(bars) - 1):
            if readiness is not None and not bool(readiness.iloc[i][column]):
                continue
            stamp = bars.index[i]
            next_bar = confirmations[i] if confirmations is not None else bars.index[i + 1]
            if pd.isna(next_bar):
                continue
            if next_bar > now or (since is not None and not backfill and stamp < since):
                continue
            if window_start is not None and (stamp < window_start or next_bar > window_end):
                continue
            if calculated.iloc[i][column] != 1:
                continue
            identity = {**source, "timestamp": stamp.isoformat(), "signal": signal,
                        "indicator_version": version}
            event_id = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            row = calculated.iloc[i]
            context = {key: finite(row.get(key)) for key in
                       ("D", "A", "M", "blueUpperBand", "blueLowerBand", "yellowUpperBand", "yellowLowerBand")}
            matches.append({**identity, "event_id": event_id, "ticker": instrument.get("notion_ticker", instrument["id"]),
                            "value_unit": "Yield %" if instrument.get("unit") == "percent_yield" else "Price",
                            "market_timezone": market_timezone(instrument),
                            "price": float(bars.iloc[i].close), "first_seen": now.isoformat(),
                            "validation": "Unvalidated", "backfill": backfill,
                            "next_bar_at": (bars.index[i + 1].isoformat() if i + 1 < len(bars) else None),
                            "confirmation_at": next_bar.isoformat(),
                            "close_status": "calendar_close" if confirmations is not None else "superseded_by_next_bar",
                            "history_start": bars.index[0].isoformat(), "technical": context,
                            "snapshot": snapshot(bars.iloc[i].close, row)})
        events.extend(matches[-limit:] if backfill else matches)
    return sorted(events, key=lambda e: (e["timestamp"], e["signal"]))
