"""Trash only historical events in the fixed Signal Events data source."""
import json
import os
from pathlib import Path
import time
import urllib.error
import urllib.request

SOURCE = "4eae824c-d660-45c0-a159-fec053ab308d"
FILTER = {"property": "Backfill", "checkbox": {"equals": True}}


class API:
    def __init__(self):
        self.token = os.environ["NOTION_TOKEN"]
        if not self.token:
            raise RuntimeError("NOTION_TOKEN is empty")
        self.last = 0.0

    def __call__(self, method, path, body=None):
        # Only reads, filtered queries and idempotent trash updates use this helper.
        for attempt in range(6):
            time.sleep(max(0, 0.4 - (time.monotonic() - self.last)))
            self.last = time.monotonic()
            req = urllib.request.Request("https://api.notion.com/v1/" + path,
                data=None if body is None else json.dumps(body).encode(), method=method,
                headers={"Authorization": "Bearer " + self.token,
                         "Notion-Version": "2025-09-03", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise RuntimeError(f"Notion HTTP {exc.code}") from None
                delay = max(2 ** attempt, float(exc.headers.get("Retry-After", "1")))
            except (urllib.error.URLError, TimeoutError, OSError):
                delay = 2 ** attempt
            if attempt == 5:
                raise RuntimeError("Notion request failed after retries; rerun safely") from None
            time.sleep(delay)


def eligible(page):
    return (page.get("parent", {}).get("data_source_id") == SOURCE
            and page.get("properties", {}).get("Backfill", {}).get("checkbox") is True
            and not page.get("in_trash", False) and not page.get("archived", False))


def clean(api, report):
    schema = api("GET", f"data_sources/{SOURCE}")
    if schema.get("properties", {}).get("Backfill", {}).get("type") != "checkbox":
        raise RuntimeError("Backfill checkbox schema mismatch")
    # Snapshot IDs before changing results, so deletion cannot shift pagination.
    ids = []
    body = {"filter": FILTER, "page_size": 100}
    while True:
        result = api("POST", f"data_sources/{SOURCE}/query", body)
        for page in result["results"]:
            if not eligible(page):
                raise RuntimeError("Query returned an unexpected page; stopping")
            ids.append(page["id"])
        if not result.get("has_more"):
            break
        body = {**body, "start_cursor": result["next_cursor"]}
    report["selected"] = len(ids)
    print(json.dumps({"selected": len(ids)}), flush=True)
    for page_id in ids:
        page = api("GET", f"pages/{page_id}")
        if not eligible(page):
            report["skipped"] += 1
            continue
        result = api("PATCH", f"pages/{page_id}", {"in_trash": True})
        if not result.get("in_trash") and not result.get("archived"):
            raise RuntimeError("Trash update not confirmed")
        report["trashed"] += 1
        if report["trashed"] % 50 == 0:
            print(json.dumps({"trashed": report["trashed"]}), flush=True)
    result = api("POST", f"data_sources/{SOURCE}/query", {"filter": FILTER, "page_size": 1})
    report["remaining_backfill_zero"] = not result["results"]
    if result["results"]:
        raise RuntimeError("Backfill records remain; rerun to reconcile")
    report["status"] = "completed"


if __name__ == "__main__":
    report = {"status": "running", "source": SOURCE, "selected": 0, "trashed": 0, "skipped": 0}
    try:
        clean(API(), report)
    except Exception as exc:
        report.update(status="failed", error=str(exc))
    Path("reports").mkdir(exist_ok=True)
    Path("reports/clear-backfill.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report), flush=True)
    raise SystemExit(0 if report["status"] == "completed" else 1)
