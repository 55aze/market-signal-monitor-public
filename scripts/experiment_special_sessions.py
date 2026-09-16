"""Read-only timestamp experiment for feeds not yet covered by deterministic close policies."""
import json
from pathlib import Path

import pandas as pd

from market_signal_monitor.scanner import fetch_worker

GROUPS = {
    "us_indices": ["SPX", "SOX", "RUT"],
    "treasury_yields": ["US02Y", "US05Y", "US10Y", "US30Y"],
    "fx_metals": ["DXY", "JPYUSD", "XAUUSD"],
    "brent": ["BRENT"],
    "vix": ["VIX"],
}
TIMEFRAMES = ("30m", "4H", "1D", "1W")


def time_label(ts):
    return pd.Timestamp(ts).strftime("%H:%M")


def summarize(bars, timeframe):
    idx = pd.DatetimeIndex(bars.index).tz_convert("UTC")
    tail = idx[-300:]
    result = {
        "returned_bars": len(idx),
        "first": idx[0].isoformat(),
        "last": idx[-1].isoformat(),
        "tail_weekdays": sorted(set(int(x) for x in tail.weekday)),
        "tail_time_labels_utc": sorted(set(time_label(x) for x in tail)),
    }
    if len(tail) > 1:
        gaps = pd.Series(tail[1:] - tail[:-1])
        counts = gaps.value_counts().head(12)
        result["common_gaps"] = [
            {"seconds": int(delta.total_seconds()), "count": int(count)}
            for delta, count in counts.items()
        ]
    if timeframe in ("30m", "4H"):
        frame = pd.DataFrame({"stamp": tail})
        frame["date"] = frame.stamp.dt.date
        sessions = []
        for day, group in frame.groupby("date"):
            sessions.append({
                "date": str(day),
                "weekday": int(pd.Timestamp(day).weekday()),
                "count": len(group),
                "first_utc": time_label(group.stamp.iloc[0]),
                "last_utc": time_label(group.stamp.iloc[-1]),
            })
        result["recent_sessions"] = sessions[-12:]
    else:
        result["recent_opens"] = [x.isoformat() for x in tail[-16:]]
    return result


def main():
    config = json.loads(Path("config/universe.json").read_text())
    instruments = {i["id"]: i for i in config["instruments"]}
    root = Path("reports/special-session-experiment")
    root.mkdir(parents=True, exist_ok=True)
    report = {"groups": GROUPS, "streams": [], "errors": []}
    wanted = [name for names in GROUPS.values() for name in names]
    for name in wanted:
        instrument = instruments[name]
        for tf in TIMEFRAMES:
            key = f"{name}:{tf}"
            try:
                bars = fetch_worker(instrument, tf, 1200, timeout=60, attempts=3)
                bars.to_csv(root / f"{name}-{tf}.csv")
                record = {
                    "stream": key,
                    "group": next(group for group, names in GROUPS.items() if name in names),
                    "exchange": instrument["exchange"],
                    "symbol": instrument["symbol"],
                    "summary": summarize(bars, tf),
                }
                report["streams"].append(record)
                print("SESSION_EXPERIMENT " + json.dumps(record))
            except Exception as exc:
                report["errors"].append({"stream": key, "error": str(exc)})
                print("SESSION_EXPERIMENT_ERROR " + json.dumps(report["errors"][-1]))
    (root / "summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"special_session_summary": {"streams": len(report["streams"]), "errors": report["errors"]}}))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
