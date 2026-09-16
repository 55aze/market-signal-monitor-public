"""Strict OHLC input; no resampling, forward filling or source substitution."""
from pathlib import Path
import numpy as np
import pandas as pd


def normalize(frame):
    frame = frame.copy()
    if frame.empty:
        raise ValueError("Empty OHLC dataset")
    frame.columns = [str(c).strip() for c in frame.columns]
    frame = frame.rename(columns={c: c.lower() for c in frame.columns
                                   if c.lower() in ("time", "open", "high", "low", "close", "volume")})
    if "time" not in frame:
        raise ValueError("CSV requires time: epoch seconds or ISO timestamp with timezone")
    t = frame.pop("time")
    if pd.api.types.is_numeric_dtype(t):
        # Pine exported time_close/first_time are milliseconds; CSV time is seconds.
        unit = "ms" if t.abs().median() > 100_000_000_000 else "s"
        index = pd.DatetimeIndex(pd.to_datetime(t, unit=unit, utc=True))
    else:
        index = pd.DatetimeIndex(pd.to_datetime(t, errors="raise"))
        if index.tz is None:
            raise ValueError("Naive timestamps rejected: export Unix time or include UTC offset")
        index = index.tz_convert("UTC")
    if index.hasnans or index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("Timestamps must be valid, unique and strictly increasing")
    frame.index = index
    frame.index.name = "time"
    for col in ("open", "high", "low", "close"):
        if col not in frame:
            raise ValueError(f"Missing OHLC column: {col}")
        frame[col] = pd.to_numeric(frame[col], errors="raise")
        if not np.isfinite(frame[col]).all():
            raise ValueError(f"Nonfinite {col}; missing data must be resolved, not filled")
    if ((frame.high < frame[["open", "close", "low"]].max(axis=1)) |
        (frame.low > frame[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("Invalid OHLC envelope")
    return frame


def read_bars(path):
    return normalize(pd.read_csv(path))


def write_frame(frame, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index_label="time", float_format="%.17g")
