"""One bounded scan; orchestration belongs to GitHub Actions or another worker."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import pandas as pd
from .data import read_bars
from .indicator import calculate
from .events import extract_events, indicator_version
from .curve import curve_closes
from .notion import Notion
from .snapshot import snapshot
from .confirmation import confirmation_times, has_close_policy, policy_id
from .freshness import assess_freshness, gap_error, scan_due
from .history import POLICY, dependency_masks, history_evidence


class InsufficientHistoryWarning(RuntimeError):
    """A configured stream cannot be evaluated because the provider has too little history.

    This is an expected per-stream limitation (for example a young ticker on 1W), not a
    scanner/runtime failure. The run should stay green while preserving the warning.
    """


def fetch_worker(instrument, timeframe, bars, timeout=60, attempts=3):
    with tempfile.TemporaryDirectory() as folder:
        output = str(Path(folder) / "bars.csv")
        command = [sys.executable, "-m", "market_signal_monitor.cli", "fetch", instrument["symbol"],
                   instrument["exchange"], timeframe, output, "--bars", str(bars)]
        if instrument.get("extended_session"):
            command.append("--extended")
        failures = []
        for attempt in range(1, attempts + 1):
            try:
                result = subprocess.run(command, env={**os.environ, "TZ": "UTC"}, capture_output=True,
                                        text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                failures.append(f"attempt {attempt}: exceeded {timeout}s")
                continue
            if result.returncode == 0:
                return read_bars(output)
            detail = " ".join((result.stderr or result.stdout or "no diagnostic").split())[-500:]
            failures.append(f"attempt {attempt}: exit {result.returncode}: {detail}")
        raise RuntimeError("TradingView fetch failed after " + str(attempts)
                           + " attempts; no source fallback; " + " | ".join(failures))


def curve_snapshots(frames):
    snapshots = {}
    for timeframe in {key[1] for key in frames}:
        maturities = ("US02Y", "US05Y", "US10Y", "US30Y")
        if any((name, timeframe) not in frames for name in maturities):
            continue
        yields, availability = {}, []
        for name in maturities:
            bars = frames[name, timeframe]
            yields[name] = bars.close.iloc[:-1]
            availability.append(pd.Series(bars.index[1:], index=bars.index[:-1], name=name))
        curve = curve_closes(yields)
        available = pd.concat(availability, axis=1, join="inner").max(axis=1)
        curve["available_at"] = available.reindex(curve.index)
        snapshots[timeframe] = curve
    return snapshots


def attach_curve(event, curves):
    curve = curves.get(event["timeframe"])
    if curve is None:
        event["curve_status"] = "unavailable_for_timeframe"
        return
    cutoff = pd.Timestamp(event.get("confirmation_at") or event["next_bar_at"])
    eligible = curve.loc[curve.available_at <= cutoff]
    if eligible.empty:
        event["curve_status"] = "no_prior_aligned_observation"
        return
    row = eligible.iloc[-1]
    event["curve_status"] = "prior_aligned_tv_yield_closes"
    event["curve"] = {"bar_timestamp": eligible.index[-1].isoformat(),
                      "available_at": row.available_at.isoformat(),
                      "age_seconds": (cutoff - row.available_at).total_seconds(),
                      "source": "TVC; yield levels in percent, spreads in bp",
                      **{key: float(value) for key, value in row.items() if key != "available_at"}}


def _warmup_for(config, timeframe):
    warmup = config.get("warmup", 500)
    if isinstance(warmup, dict):
        return int(warmup.get(timeframe, warmup.get("default", 500)))
    return int(warmup)


def _expected_us_daily_open_candidate(instrument, timeframe, stamp, fetch_now):
    """Allow only the known TV pre-open placeholder: today's 09:30 ET US daily label."""
    if timeframe != "1D" or instrument.get("exchange") not in {"NASDAQ", "NYSE", "AMEX", "CBOE"}:
        return False
    wall = pd.Timestamp(stamp).tz_convert("America/New_York")
    now_wall = pd.Timestamp(fetch_now).tz_convert("America/New_York")
    return (wall.date() == now_wall.date() and wall.hour == 9 and wall.minute == 30)


def run(config, *, mode="dry-run", since=None, selected=None, limit=None,
        fetcher=fetch_worker, client=None, now=None, status_store=None, ticker_store=None):
    run_started = time.monotonic()
    if mode not in ("dry-run", "live", "backfill"):
        raise ValueError("Unknown mode")
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    if mode == "backfill" and "backfill_months" in config:
        from .backfill import run_backfill
        if client is None:
            raise ValueError("Notion client required for writes")
        return run_backfill(config, client=client, fetcher=fetcher,
                            selected=selected, limit=limit, now=now)
    historical = mode == "backfill" or (mode == "dry-run" and not since)
    if mode == "live" and not since:
        raise ValueError("Set MONITOR_SINCE to a fixed UTC activation time before live scans")
    if since:
        start = pd.Timestamp(since)
        if start.tzinfo is None or start > now:
            raise ValueError("MONITOR_SINCE must include a timezone and not be in the future")
    instruments = config["instruments"]
    if selected:
        unknown = set(selected) - {i["id"] for i in instruments}
        if unknown:
            raise ValueError(f"Unknown instruments: {sorted(unknown)}")
        instruments = [i for i in instruments if i["id"] in selected]
    if client and mode != "dry-run":
        client.preflight()
    elif mode != "dry-run":
        raise ValueError("Notion client required for writes")
    if mode == "live":
        if status_store is None:
            from .backfill import StatusStore
            source = config.get("notion", {}).get("status_data_source")
            if not source:
                raise ValueError("Live scan requires notion.status_data_source")
            status_store = StatusStore(client, source)
        status_store.preflight()
    engine_config = config.get("ticker_engine", {})
    engine_enabled = mode == "live" and engine_config.get("enabled", False)
    ticker_states, ticker_batches = {}, {}
    receipts = []
    if engine_enabled:
        from .state_store import TickerStore
        from .state_pipeline import observation_batch, process_ticker
        from .state_engine import policy, acknowledge
        policy(engine_config.get("policy"))
        if ticker_store is None:
            ticker_store = TickerStore(client, engine_config["state_data_source"])
        ticker_store.preflight()
        if engine_config.get("report_page"):
            from .report_packet import delivery_receipt
            receipts = delivery_receipt(client, engine_config["report_page"])
        for instrument in instruments:
            ticker = instrument.get("notion_ticker", instrument["id"])
            ticker_states[ticker] = acknowledge(ticker_store.load(ticker, now.isoformat()), receipts)
            ticker_batches[ticker] = {}
    report = {"run_at": now.isoformat(), "mode": mode, "validation": "Unvalidated",
              "status": "running", "streams": [], "events": [], "created": 0, "existing": 0,
              "warnings": [], "errors": []}
    frames, live_streams = {}, []
    version = indicator_version(config["pine_sha256"])
    for instrument in instruments:
        for timeframe in instrument.get("timeframes", config["timeframes"]):
            key = f"{instrument['id']}:{timeframe}"
            ticker = instrument.get("notion_ticker", instrument["id"])
            status_page = previous_through = None
            try:
                if status_store is not None:
                    status_page, previous_through = status_store.load_live(key)
                    if (mode == "live" and config.get("schedule_by_close", False)
                            and not scan_due(instrument, timeframe, previous_through, now)):
                        report["streams"].append({"stream": key, "status": "skipped_not_due",
                            "latest_processed_bar": previous_through.isoformat()})
                        continue
                    status_store.live_attempt(status_page, pd.Timestamp(datetime.now(timezone.utc)))
                fetch_started = time.monotonic()
                bars = fetcher(instrument, timeframe, config.get("bars", 5000))
                fetch_seconds = time.monotonic() - fetch_started
                if bars.empty:
                    raise InsufficientHistoryWarning("Provider returned no bars")
                fetch_now = pd.Timestamp(datetime.now(timezone.utc))
                close_policy = has_close_policy(instrument)
                future_last = bars.index[-1] > fetch_now
                allowed_preopen_daily = _expected_us_daily_open_candidate(
                    instrument, timeframe, bars.index[-1], fetch_now)
                if future_last and not close_policy and not allowed_preopen_daily:
                    raise ValueError("Provider returned a future bar timestamp")
                confirmation_start = pd.Timestamp(since) if since else bars.index[max(0, len(bars) - 1000)]
                confirmations = confirmation_times(
                    bars, instrument, timeframe, strict_since=confirmation_start
                ) if close_policy else None
                latest_processed = (max((stamp for stamp, close in zip(bars.index, confirmations)
                    if close <= fetch_now), default=pd.NaT) if confirmations is not None else (bars.index[-2] if len(bars) > 1 else pd.NaT))
                if pd.isna(latest_processed):
                    raise InsufficientHistoryWarning("No confirmed bar is currently available")

                if previous_through is not None:
                    if latest_processed < previous_through:
                        raise RuntimeError("Provider history ends before the durable live checkpoint")
                    if previous_through < bars.index[0] or previous_through not in bars.index:
                        raise RuntimeError("Durable live checkpoint is absent from fetched history; gap cannot be ruled out")

                freshness = assess_freshness(
                    bars.index, instrument, timeframe, fetch_now, latest_processed,
                    previous_through=previous_through, enabled=close_policy
                )
                if freshness["status"] == "interior_gap":
                    raise RuntimeError(gap_error(freshness))

                compute_started = time.monotonic()
                calculated = calculate(bars)
                dependency_policy = config.get("history_policy") == POLICY
                readiness = dependency_masks(calculated) if dependency_policy else None
                warmup = 0 if dependency_policy else _warmup_for(config, timeframe)
                try:
                    events = extract_events(bars, calculated, instrument, timeframe, version, fetch_now,
                                            since=since, backfill=historical,
                                            limit=limit if limit is not None else config.get("backfill_limits", {}).get(timeframe, 10),
                                            warmup=warmup, confirmations=confirmations, readiness=readiness)
                except ValueError as exc:
                    if str(exc) == "Insufficient history after warmup and latest-bar exclusion":
                        raise InsufficientHistoryWarning(str(exc)) from None
                    raise
                current_structure = snapshot(bars.loc[latest_processed, "close"], calculated.loc[latest_processed])
                current_structure["freshness"] = freshness
                evidence = (history_evidence(bars, calculated, readiness, latest_processed,
                            config.get("bars", 5000)) if dependency_policy else None)
                if evidence is not None:
                    current_structure["history"] = evidence
                if engine_enabled:
                    ticker_batches[ticker][timeframe] = {
                        "bars": observation_batch(bars, calculated, instrument, timeframe,
                                                  confirmations, fetch_now),
                        "error": freshness["status"] == "stale_tail"}
                frames[instrument["id"], timeframe] = bars
                report["events"].extend(events)
                stream = {"stream": key, "status": "fetched_and_computed", "bars": len(bars),
                          "fetch_seconds": round(fetch_seconds, 3),
                          "compute_seconds": round(time.monotonic() - compute_started, 3),
                          "confirmation_policy": policy_id(instrument),
                          "latest_returned_bar": bars.index[-1].isoformat(),
                          "latest_processed_bar": latest_processed.isoformat(),
                          "freshness": freshness, "events": len(events)}
                if evidence is not None:
                    stream["history"] = evidence
                    if not all(evidence["signal_dependencies_ready"].values()):
                        report["warnings"].append({"stream": key, "kind": "signal_history_unready",
                            "warning": "Structure computed; some signal crossing dependencies are not present",
                            "history": evidence})
                if freshness["status"] == "stale_tail":
                    warning = (
                        f"{freshness['missing_count']} expected confirmed bar(s) missing after "
                        f"{freshness['actual_latest']}; expected latest {freshness['expected_latest']}"
                    )
                    report["warnings"].append(
                        {"stream": key, "warning": warning, "kind": "stale_tail",
                         "freshness": freshness}
                    )
                if dependency_policy and since:
                    for signal, first in evidence["first_dependency_ready_bar"].items():
                        if first is None or pd.Timestamp(since) < pd.Timestamp(first):
                            stream.setdefault("signal_coverage_warning", {})[signal] = (
                                "Activation predates observed signal dependencies; no full signal coverage claim")
                if since and pd.Timestamp(since) < bars.index[warmup]:
                    stream["coverage_warning"] = "Activation predates usable fetched history; old gaps cannot be ruled out"
                report["streams"].append(stream)
                if status_store is not None:
                    live_streams.append({"key": key, "page": status_page,
                                         "through": latest_processed, "events": events,
                                         "structure": current_structure})
            except InsufficientHistoryWarning as exc:
                if engine_enabled:
                    ticker_batches[ticker][timeframe] = {"error": True}
                warning = {"stream": key, "warning": str(exc), "kind": "insufficient_history"}
                report["warnings"].append(warning)
                report["streams"].append({"stream": key, "status": "skipped_insufficient_history",
                                          "warning": str(exc)})
                if status_store is not None and status_page:
                    try:
                        status_store.live_failure(status_page, "Insufficient History", str(exc),
                                                  pd.Timestamp(datetime.now(timezone.utc)))
                    except (ValueError, RuntimeError, OSError) as status_exc:
                        report["errors"].append({"stream": key, "error": f"Status write failed: {status_exc}"})
            except (ValueError, RuntimeError, OSError) as exc:
                if engine_enabled:
                    ticker_batches[ticker][timeframe] = {"error": True}
                if str(exc) == "Provider returned a future bar timestamp":
                    warning = {"stream": key, "kind": "future_tail_skipped",
                               "warning": str(exc)}
                    report["warnings"].append(warning)
                    report["streams"].append({"stream": key, "status": "skipped_future_bar",
                                              "warning": str(exc)})
                else:
                    report["errors"].append({"stream": key, "error": str(exc)})
                    report["streams"].append({"stream": key, "status": "failed"})
                    if status_store is not None and status_page:
                        try:
                            status_store.live_failure(status_page, "Fetch Failed", str(exc),
                                                      pd.Timestamp(datetime.now(timezone.utc)))
                        except (ValueError, RuntimeError, OSError) as status_exc:
                            report["errors"].append({"stream": key, "error": f"Status write failed: {status_exc}"})
    curves = curve_snapshots(frames)
    report["curve_latest"] = {}
    for timeframe, curve in curves.items():
        if not curve.empty:
            row = curve.iloc[-1]
            report["curve_latest"][timeframe] = {"bar_timestamp": curve.index[-1].isoformat(),
                "available_at": row.available_at.isoformat(),
                **{k: float(v) for k, v in row.items() if k != "available_at"}}
    for event in report["events"]:
        attach_curve(event, curves)
    delivery_started = time.monotonic()
    if status_store is not None:
        for position, item in enumerate(live_streams):
            try:
                for event in item["events"]:
                    created = client.append(event)
                    report["created" if created else "existing"] += 1
                status_store.live_success(item["page"], item["through"],
                                          pd.Timestamp(datetime.now(timezone.utc)),
                                          structure=item["structure"])
            except (ValueError, RuntimeError, OSError) as exc:
                report["errors"].append({"stream": item["key"], "error": str(exc)})
                try:
                    status_store.live_failure(item["page"], "Write Failed", str(exc),
                                              pd.Timestamp(datetime.now(timezone.utc)))
                except (ValueError, RuntimeError, OSError) as status_exc:
                    report["errors"].append({"stream": item["key"],
                                             "error": f"Status write failed: {status_exc}"})
                for skipped in live_streams[position + 1:]:
                    reason = f"Delivery skipped after earlier write failure in {item['key']}"
                    try:
                        status_store.live_failure(skipped["page"], "Write Failed", reason,
                                                  pd.Timestamp(datetime.now(timezone.utc)))
                    except (ValueError, RuntimeError, OSError) as status_exc:
                        report["errors"].append({"stream": skipped["key"],
                                                 "error": f"Status write failed: {status_exc}"})
                break
    elif client and mode != "dry-run":
        for event in report["events"]:
            try:
                created = client.append(event)
                report["created" if created else "existing"] += 1
            except (ValueError, RuntimeError) as exc:
                report["errors"].append({"event_id": event["event_id"], "error": str(exc)})
                break
    if engine_enabled:
        report["ticker_engine"] = {"saved": 0, "uncertain": 0, "pending": 0}
        # Raw delivery must complete first. Ticker checkpoints are independent,
        # so a failed state PATCH is replayable from fetched native history.
        if not report["errors"]:
            for ticker, state in ticker_states.items():
                try:
                    result = process_ticker(state, ticker_batches[ticker], report["events"],
                                            pd.Timestamp(datetime.now(timezone.utc)).isoformat(),
                                            engine_config.get("policy"))
                    ticker_store.save(ticker, result)
                    ticker_states[ticker] = result
                    report["ticker_engine"]["saved"] += 1
                    report["ticker_engine"]["uncertain"] += bool(result["uncertainty"])
                    report["ticker_engine"]["pending"] += len(result["pending"])
                except (ValueError, RuntimeError, OSError) as exc:
                    report["errors"].append({"ticker": ticker, "error": str(exc)})
                    break
        if not report["errors"] and engine_config.get("report_page"):
            try:
                from .report_packet import packet, publish_packet
                window = pd.Timedelta(hours=engine_config.get("theme_window_hours", 72))
                # Manual subset scans must not hide unrelated pending events.
                packet_states = [ticker_states.get(ticker) or ticker_store.load(ticker, now.isoformat())
                                 for ticker in ticker_store.rows]
                packet_now = pd.Timestamp(datetime.now(timezone.utc))
                data = packet(packet_states, now=packet_now.isoformat(),
                              since=(packet_now - window).isoformat(),
                              membership=config.get("theme_membership"),
                              exposure=config.get("exposure_groups"))
                publish_packet(client, engine_config["report_page"], data)
            except (ValueError, RuntimeError, OSError) as exc:
                report["errors"].append({"component": "report_packet", "error": str(exc)})
    if report["errors"]:
        report["status"] = "partial_failure"
    elif report["warnings"]:
        report["status"] = "completed_with_warnings"
    else:
        report["status"] = "completed"
    report["delivery_seconds"] = round(time.monotonic() - delivery_started, 3)
    report["duration_seconds"] = round(time.monotonic() - run_started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description="Scan native TV bars and append unvalidated Pine events")
    parser.add_argument("--config", default="config/universe.json")
    parser.add_argument("--mode", choices=["dry-run", "live", "backfill"], default="dry-run")
    parser.add_argument("--since", default=os.environ.get("MONITOR_SINCE") or None)
    parser.add_argument("--instruments", help="Comma-separated IDs, e.g. QQQ; omitted scans configured universe")
    parser.add_argument("--limit", type=int, default=None, help="Override configured history limit for every timeframe (per signal)")
    parser.add_argument("--output", default="reports/scan.json")
    args = parser.parse_args()
    try:
        config = json.loads(Path(args.config).read_text())
        client = None
        if args.mode != "dry-run":
            client = Notion(os.environ.get("NOTION_TOKEN"), config["notion"]["events_data_source"],
                            config["notion"]["tickers_data_source"])
        since = args.since or (config.get("monitor_since") if args.mode == "live" else None)
        report = run(config, mode=args.mode, since=since,
                     selected=args.instruments.split(",") if args.instruments else None,
                     limit=args.limit, client=client)
    except (ValueError, RuntimeError, OSError) as exc:
        report = {"status": "failed", "errors": [{"error": str(exc)}]}
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))
    print(json.dumps({k: v for k, v in report.items() if k != "events"}))
    return 0 if report["status"] in ("completed", "completed_with_warnings") else 2


if __name__ == "__main__":
    raise SystemExit(main())
