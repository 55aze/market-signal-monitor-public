import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from .data import read_bars, write_frame
from .indicator import calculate
from .parity import compare


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description="Pine calculation and comparison workbench")
    sub = p.add_subparsers(dest="command", required=True)
    calc = sub.add_parser("calculate")
    calc.add_argument("input")
    calc.add_argument("output")
    calc.add_argument("--blue", type=int, default=26)
    calc.add_argument("--yellow", type=int, default=89)
    check = sub.add_parser("compare")
    check.add_argument("reference")
    check.add_argument("candidate")
    check.add_argument("output")
    check.add_argument("--instrument", required=True, help="Exact EXCHANGE:SYMBOL")
    check.add_argument("--timeframe", choices=["30m", "1H", "4H", "1D", "1W"], required=True)
    check.add_argument("--warmup", type=int, default=500)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("symbol")
    fetch.add_argument("exchange")
    fetch.add_argument("timeframe", choices=["30m", "1H", "4H", "1D", "1W"])
    fetch.add_argument("output")
    fetch.add_argument("--bars", type=int, default=5000)
    fetch.add_argument("--extended", action="store_true")
    args = p.parse_args()
    try:
        if args.command == "calculate":
            bars = read_bars(args.input)
            result = calculate(bars, args.blue, args.yellow)
            # Drop reference indicator outputs before joining Python outputs.
            kept = [c for c in bars if c in ("open", "high", "low", "close", "volume", "close_status")]
            result = bars[kept].drop(columns=[c for c in kept if c in result]).join(result)
            write_frame(result, args.output)
            meta = {"status": "computed_not_tv_validated", "input_sha256": digest(args.input),
                    "input": str(Path(args.input).resolve()), "bars": len(result),
                    "parameters": {"n1": args.blue, "n2": args.yellow},
                    "computed_at": datetime.now(timezone.utc).isoformat()}
            Path(args.output + ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            print(json.dumps(meta))
        elif args.command == "compare":
            report = compare(read_bars(args.reference), read_bars(args.candidate), args.warmup)
            report.update(instrument=args.instrument, timeframe=args.timeframe,
                          reference_sha256=digest(args.reference), candidate_sha256=digest(args.candidate))
            Path(args.output).parent.mkdir(parents=True, exist_ok=True)
            Path(args.output).write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
            print(json.dumps({"status": report["status"], "signals": report["signals"]}))
            return 0 if report["status"] == "match_on_supplied_sample" else 2
        else:
            from .tradingview import fetch as get_hist, UPSTREAM_COMMIT
            bars = get_hist(args.symbol, args.exchange, args.timeframe, args.bars, args.extended)
            write_frame(bars, args.output)
            meta = {"provider": "tvDatafeed_nologin", "upstream_reviewed_commit": UPSTREAM_COMMIT,
                    "symbol": f"{args.exchange}:{args.symbol}", "timeframe": args.timeframe,
                    "session": "extended" if args.extended else "regular", "adjustment": "splits",
                    "requested_bars": args.bars, "returned_bars": len(bars),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "sha256": digest(args.output), "chart_parity_verified": False}
            Path(args.output + ".json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            print(json.dumps(meta))
    except (ValueError, RuntimeError, ImportError, OSError) as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
