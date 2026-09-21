# Market signal monitor

Python implementation of Pine market signals. All outputs remain Unvalidated until TradingView parity is verified.

This publication snapshot includes formulas, the watchlist, source and tests. It excludes private Notion identifiers, reports and old Git history. Configure repository secrets NOTION_TOKEN and MONITOR_CONFIG_JSON (the complete private universe configuration). Never print either secret. The initial workflow is manual only, so it cannot overlap an existing production writer. Enable a single production schedule only after validating the migration and stopping the old scheduler.

Install: `python -m pip install -e . -r requirements-tv.txt`

Test: `python -m unittest discover -s tests -v`

The example configuration is a template, not production configuration. Persist the original monitor_since and Notion checkpoints. Higher-timeframe close-aware scheduling and duration metrics are included.

Optional deterministic ticker lifecycle and the thin six-hour ChatGPT reporter are
documented in [docs/state-engine.md](docs/state-engine.md). This feature is disabled
until private schema/configuration is provisioned and the old LLM state writer is
retired. The reporter prompt is in
[docs/trading-signal-watch-prompt.md](docs/trading-signal-watch-prompt.md).
