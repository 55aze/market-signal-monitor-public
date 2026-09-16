"""Validate scheduler metadata without touching scanner state or Notion.

External live dispatches are intentionally stricter than manual dispatches:
- they must identify themselves as external;
- they must carry an RFC3339 scheduled_for timestamp;
- they may not override the production instrument universe;
- backfill is never permitted from the scheduler.

The script writes an audit-only JSON artifact. It does not decide whether monitoring is
paused; the workflow job condition handles MONITOR_ENABLED before any runner is started.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path


ALLOWED_MODES = {"dry-run", "live", "backfill"}
ALLOWED_SOURCES = {"manual", "external"}


def _parse_rfc3339(value: str) -> datetime:
    text = value.strip()
    if not text:
        raise ValueError("external dispatch requires scheduled_for")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scheduled_for must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise ValueError("scheduled_for must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate(event_name: str, mode: str, trigger_source: str, scheduled_for: str,
             instruments: str) -> dict:
    if mode not in ALLOWED_MODES:
        raise ValueError(f"unknown mode: {mode}")

    if event_name == "workflow_dispatch":
        source = trigger_source or "manual"
        if source not in ALLOWED_SOURCES:
            raise ValueError(f"unknown trigger_source: {source}")
        if source == "external":
            scheduled = _parse_rfc3339(scheduled_for)
            if mode == "backfill":
                raise ValueError("external scheduler may not run backfill")
            if mode == "live" and instruments.strip():
                raise ValueError(
                    "external live dispatch may not override instruments; use MONITOR_INSTRUMENTS/full universe"
                )
            scheduled_text = scheduled.isoformat().replace("+00:00", "Z")
        else:
            if scheduled_for.strip():
                raise ValueError("manual dispatch must not set scheduled_for")
            scheduled_text = None
    else:
        # Inputs do not exist for push/schedule events; label them for audit only.
        source = "github-schedule" if event_name == "schedule" else event_name
        scheduled_text = None

    return {
        "event_name": event_name,
        "mode": mode,
        "trigger_source": source,
        "scheduled_for": scheduled_text,
        "instruments_override": instruments.strip() or None,
        "validated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--trigger-source", default="")
    parser.add_argument("--scheduled-for", default="")
    parser.add_argument("--instruments", default="")
    parser.add_argument("--output", default="reports/trigger.json")
    args = parser.parse_args()

    audit = validate(args.event_name, args.mode, args.trigger_source,
                     args.scheduled_for, args.instruments)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
