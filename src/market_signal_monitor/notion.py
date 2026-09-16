"""Notion API transport. Credentials come only from the runtime environment.

    Single writer required: the API has no unique constraint on Event ID.
    Creates are not blindly retried on ambiguous network/server failures.
"""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo
import requests
from .snapshot import notion_properties, SCHEMA


def rich(text):
    text = str(text)
    return [{"type": "text", "text": {"content": text[i:i + 1900]}}
            for i in range(0, len(text), 1900)]


def market_time(event):
    zone = event.get("market_timezone")
    if not zone:
        zone = "America/New_York" if (event["exchange"], event["symbol"]) == ("NASDAQ", "QQQ") else "UTC"
    stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Market display requires a timezone-aware timestamp")
    return stamp.astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d %H:%M %Z")


def event_properties(event, ticker_id):
    return {
        "Event": {"title": rich(f"{event['ticker']} {event['timeframe']} {event['signal']} {market_time(event)}")},
        "Bar Time (Market)": {"rich_text": rich(market_time(event))},
        "Event ID": {"rich_text": rich(event["event_id"])},
        "Symbol": {"rich_text": rich(event["ticker"])},
        "Signal": {"select": {"name": event["signal"]}},
        "Timeframe": {"select": {"name": event["timeframe"]}},
        "Timestamp": {"date": {"start": event["timestamp"]}},
        "First Seen": {"date": {"start": event["first_seen"]}},
        "Confirmation Evidence At": {"date": {"start": event.get("confirmation_at") or event["next_bar_at"]}},
        "Value Unit": {"select": {"name": event.get("value_unit", "Price")}},
        "Record Origin": {"select": {"name": "Historical Backfill" if event["backfill"] else "Live Scan"}},
        "Report Status": {"select": {"name": "Historical - Silent" if event["backfill"] else "Pending"}},
        "Price": {"number": event["price"]},
        "Validation": {"select": {"name": "Unvalidated"}},
        "Backfill": {"checkbox": event["backfill"]},
        "Surfaced": {"checkbox": False},
        "Source": {"rich_text": rich(f"tvDatafeed {event['exchange']}:{event['symbol']} / {event['session']} / splits")},
        "Indicator Version": {"rich_text": rich(event["indicator_version"])},
        "Raw Payload": {"rich_text": rich(json.dumps(event, ensure_ascii=False, allow_nan=False))},
        "Ticker": {"relation": [{"id": ticker_id}]},
        **(notion_properties(event["snapshot"]) if "snapshot" in event else {}),
    }


class Notion:
    def __init__(self, token, events_source, tickers_source, session=None):
        if not token:
            raise ValueError("NOTION_TOKEN is missing")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}",
                                     "Notion-Version": "2025-09-03", "Content-Type": "application/json"})
        self.events_source, self.tickers_source = events_source, tickers_source
        self.tickers = {}
        self.last_request = 0.0

    def request(self, method, path, body=None):
        safe_retry = method == "GET" or path.endswith("/query")
        for attempt in range(4):
            time.sleep(max(0, 0.36 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.request(method, "https://api.notion.com/v1/" + path,
                                                json=body, timeout=(10, 30))
            except requests.RequestException:
                if safe_retry and attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError("Notion network failure; any create outcome is unknown. Re-run to reconcile Event IDs.") from None
            if response.status_code == 429 or (safe_retry and response.status_code >= 500):
                if attempt < 3:
                    time.sleep(min(30, max(1, float(response.headers.get("Retry-After", 2 ** attempt)))))
                    continue
            if not response.ok:
                # Do not print HTTP bodies or headers, which may contain sensitive context.
                raise RuntimeError(f"Notion HTTP {response.status_code} on {method} {path}")
            return response.json()
        raise RuntimeError("Notion retry limit reached")

    def query(self, source, filters):
        body = {"filter": filters, "page_size": 100}
        while True:
            result = self.request("POST", f"data_sources/{source}/query", body)
            yield from result["results"]
            if not result.get("has_more"):
                return
            body["start_cursor"] = result["next_cursor"]

    def preflight(self):
        required = {"Event": "title", "Event ID": "rich_text", "Symbol": "rich_text",
                    "Signal": "select", "Timeframe": "select", "Timestamp": "date",
                    "First Seen": "date", "Price": "number", "Validation": "select",
                    "Backfill": "checkbox", "Surfaced": "checkbox", "Source": "rich_text",
                    "Indicator Version": "rich_text", "Raw Payload": "rich_text", "Ticker": "relation"}
        required.update(SCHEMA)
        required["Bar Time (Market)"] = "rich_text"
        required.update({"Confirmation Evidence At": "date", "Value Unit": "select",
                         "Record Origin": "select", "Report Status": "select"})
        for source, schema in ((self.events_source, required),
                               (self.tickers_source, {"Ticker": "title", "Active": "checkbox"})):
            actual = self.request("GET", f"data_sources/{source}")["properties"]
            wrong = [name for name, kind in schema.items() if actual.get(name, {}).get("type") != kind]
            if wrong:
                raise ValueError(f"Notion schema mismatch: {', '.join(wrong)}")

    def ticker_id(self, name):
        if name not in self.tickers:
            rows = list(self.query(self.tickers_source, {"property": "Ticker", "title": {"equals": name}}))
            if len(rows) > 1:
                raise ValueError(f"Ambiguous Notion ticker: {name}")
            if rows:
                row = rows[0]
            else:
                row = self.request("POST", "pages", {"parent": {"data_source_id": self.tickers_source},
                    "properties": {"Ticker": {"title": rich(name)}, "Active": {"checkbox": True}}})
            self.tickers[name] = row["id"]
        return self.tickers[name]

    def append(self, event):
        rows = list(self.query(self.events_source,
                    {"property": "Event ID", "rich_text": {"equals": event["event_id"]}}))
        if rows:
            return False
        ticker_id = self.ticker_id(event["ticker"])
        self.request("POST", "pages", {"parent": {"data_source_id": self.events_source},
                                       "properties": event_properties(event, ticker_id)})
        return True
