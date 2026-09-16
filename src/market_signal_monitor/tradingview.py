"""No-login tvDatafeed adapter. Execute in a dedicated UTC worker process.

Upstream parses Unix timestamps through local datetime.fromtimestamp, hence
the UTC guard. Latest row is conservatively unconfirmed, even on weekends.
Earlier rows are only 'superseded_by_next_bar', not server-confirmed flags.

TradingView series requests are bounded, but the websocket supports
``request_more_data`` on the same series. We use that to retrieve older history
in bounded chunks, then sort and deduplicate by timestamp.
"""
import os
import time
import pandas as pd
from .data import normalize

UPSTREAM_COMMIT = "e6f6aaa7de439ac6e454d9b26d2760ded8dc4923"
MAX_SERIES_BARS = 5000


def _recv_until_completed(client):
    raw_data = ""
    while True:
        result = client.ws.recv()
        raw_data += result + "\n"
        if "series_completed" in result:
            return raw_data


def _request_more(client, count, symbol):
    """Request older bars for the existing ``s1`` series.

    The pinned tvDatafeed version exposes no public pagination method, so this
    deliberately uses its pinned private websocket helpers. ``UPSTREAM_COMMIT``
    makes that dependency explicit and reviewable.
    """
    send = getattr(client, "_TvDatafeed__send_message")
    parse = getattr(client, "_TvDatafeed__create_df")
    send("request_more_data", [client.chart_session, "s1", int(count)])
    raw = _recv_until_completed(client)
    return parse(raw, symbol)


def fetch(symbol, exchange, timeframe, n_bars=5000, extended_session=False):
    if os.environ.get("TZ") != "UTC":
        raise RuntimeError("Run fetch in a dedicated process with TZ=UTC")
    if hasattr(time, "tzset"):
        time.tzset()
    if n_bars < 1:
        raise ValueError("n_bars must be >= 1")
    from tvDatafeed import TvDatafeed, Interval
    intervals = {"30m": Interval.in_30_minute, "1H": Interval.in_1_hour,
                 "4H": Interval.in_4_hour, "1D": Interval.in_daily, "1W": Interval.in_weekly}
    if timeframe not in intervals:
        raise ValueError("Supported timeframes: 30m, 1H, 4H, 1D, 1W")

    client = TvDatafeed()
    full_symbol = symbol if ":" in symbol else f"{exchange}:{symbol}"
    try:
        first = min(int(n_bars), MAX_SERIES_BARS)
        raw = client.get_hist(symbol=symbol, exchange=exchange, interval=intervals[timeframe],
                              n_bars=first, extended_session=extended_session)
        if raw is None or raw.empty:
            raise RuntimeError("tvDatafeed returned no data; no fallback was used")

        frames = [raw]
        combined = raw
        while len(combined) < n_bars:
            remaining = int(n_bars) - len(combined)
            batch = _request_more(client, min(MAX_SERIES_BARS, remaining), full_symbol)
            if batch is None or batch.empty:
                break
            before = len(combined)
            frames.append(batch)
            combined = pd.concat(frames).sort_index()
            combined = combined[~combined.index.duplicated(keep="last")]
            if len(combined) <= before:
                break
        raw = combined.iloc[-int(n_bars):]
    finally:
        if getattr(client, "ws", None) is not None:
            client.ws.close()

    raw = raw.reset_index().rename(columns={"datetime": "time"})
    raw["time"] = raw["time"].dt.tz_localize("UTC")
    result = normalize(raw)
    result["close_status"] = "superseded_by_next_bar"
    result.iloc[-1, result.columns.get_loc("close_status")] = "unknown_latest_bar"
    return result
