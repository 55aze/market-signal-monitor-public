# Trading Signal Watch: durable response evidence v3

This is the executable reporter contract for the existing six-hour task. The task's configuration supplies the exact repository commit, report row, collections, automation ID, helper SHA-256, and any one-time migration quarantine. These are configuration, not proof of delivery.

## Scope and authority

Keep the existing schedule, enabled status, timezone, instrument universe, producer selection, and Python-owned Engine State. Never create a Site or another scheduled task. The reporter's only external write is the configured Notion report row's `Delivered IDs`. Do not modify Packet, Signal History, Scan Status, or GitHub from a scheduled report. This document replaces conflicting older delivery gates; the presentation rules below preserve the existing report design.

The producer already consumes valid receipts. The defect addressed here is that a later reporter cannot read an earlier ephemeral HTML body. A path plus Receipt is not complete evidence, and absence of proof is not proof an event is new.

## Each run: deterministic gate before presentation

1. Discover and use `Automations.peek` for the configured automation ID. Read `run_details.latest_result` and its complete `content_text`, `content_truncated`, `run_id`, and `created_at`. Cross-check `last_run` rather than treating `completed` or `last_run_time` as delivery success. A latest result can be older than last_run. For a current/in-progress run or retry, pass the actual current run ID to the helper and reject a result with that same ID. When no current run ID can be verified, never invent one; use the actual execution-start time and verify the result predates this execution. An earlier output of a known current retry is never a prior report.
2. Fetch the configured Notion report data-source schema, then read BOTH Packet and persisted Delivered IDs in full. Fetch can be used when SQL is unavailable. Decode the known Notion Markdown presentation layer once; do not strip arbitrary JSON escapes or double-parse an outer JSON string. An explicitly empty property is empty; unreadable/truncated/malformed is RECEIPT_READ_FAILED, not empty.
3. Retrieve `scripts/reporter_delivery.py` from the exact commit configured in the task through the GitHub connector. Save the returned complete contents locally and verify the configured SHA-256 before execution. This is standard-library Python; no installation, secret, or network call is required. If retrieval, integrity, or execution is unavailable, stop with a short TOOL_UNAVAILABLE/READ_FAILED; do not improvise IDs or delivery decisions.
4. Save the actual inputs as packet.json, delivered.txt (plain persisted text), latest-result.json, and quarantine.json. Run the helper `plan`, with an actual timezone-aware now and current-run-id when verified. Alternatively import the same pinned module and call its functions directly. Never rebuild missing Packet items from history.

Example (paths are local inputs, not evidence):

```sh
python reporter_delivery.py plan --packet packet.json --delivered delivered.txt \
  --latest-result latest-result.json --quarantine quarantine.json --now "$NOW" --out plan.json
```

The helper validates the entire parent Packet v2 before suppression: presentation marker, all fields and item lines, exact ordered ID equality, uniqueness, included/remaining counts, hash identity, final packet_end, finite values, and timezone-aware event times. A missing Packet or as_of older than two hours is PACKET_NOT_READY. Underlying stream freshness is separate from Packet generation time.

Receipt identity is the first 24 SHA256 hex characters of Python `json.dumps(parts, sort_keys=True)` using its DEFAULT SPACES: `identity('packet', sorted(unique(ids)))`. Persisted receipts need explicit delivered_at; null requires delivery_evidence=prior_complete_report; non-null timestamps must be timezone-aware and not future. Supported legacy arrays/CSV suppress exact IDs without acquiring an invented time or provenance.

## Three distinct states, not two

- **Confirmed:** valid persisted receipt or an actually read complete prior response.
- **Unconfirmed prior output:** a real legacy Receipt exists but its complete body cannot be read. Quarantine these exact IDs from automatic rebroadcast; do NOT acknowledge them or delete them from the producer outbox.
- **Unreported:** fully validated Packet items outside both sets. Deliver all of these, preserving parent order in metadata. Display sorting can differ.

The task's migration quarantine identifies the already diagnosed incident. It is not a delivery receipt. Preserve it across runs, and carry additional unconfirmed claims in the evidence manifest. Complete recovered evidence takes precedence. Remove quarantine only after recovering a complete original response, or explicit authorization for a one-time re-delivery that creates fresh evidence. Never reconstruct an old response from current Packet data and call it delivered.

A partial overlap is not ACK_PENDING. A new subset gets its OWN computed receipt identity; never reuse the mixed parent packet_id. If only the already disclosed migration quarantine remains, stay silent rather than repeatedly issuing 0-new reports or the same warning. Newly discovered uncertainty or quarantine that fills a batch gets one operational warning. It is not a market signal.

## Acknowledge prior output, not this run

If plan.ack_receipt is non-null, it is based on complete prior response data. Write it before preparing the new report, preserving existing unconsumed evidence through the helper's deterministic union. Never use the current response, a file path, an ID-only legacy Receipt, or a mere run-completed flag as acknowledgement evidence. Preserve explicit null when actual delivery time is unknown; automation created_at is not delivered_at.

`notion_transport(receipt, now)` implements JSON serialization ONCE plus inline escaping of `[\\*~`$\[\]<>{}|^]`. Pass its output as a STRING to `properties['Delivered IDs']` in Notion update_properties. Never pass a nested receipt object, ID array, rich_text wrapper, or double-serialized JSON. Fetch schema first.

Read back persisted plain text and call `verify_readback(expected, actual, now)`. Use notion_markdown=True only for the known fetch/rows presentation layer, never raw SQL. Compare the entire object, including item_ids ORDER and delivered_at. A tool success without exact readback is not acknowledgement. Retry at most once with the identical receipt; then WRITE_FAILED. On a write failure, do not publish another new signal batch. Preserve the complete original prior result using the helper's `recovery` command in the error response. It emits AcknowledgementRecovery-v1, not a new Receipt; repeated failures flatten to the original result and cannot promote an ID-only claim. This prevents an error response from destroying the sole recoverable prior body.

Before writing, re-read if the persisted receipt may have changed; recompute a union rather than overwrite competing evidence. After any successful write, re-read BOTH Packet and Delivered IDs and rerun plan. Do not assume the producer has consumed the acknowledgement yet. Do not prune old persisted evidence merely because a bounded Packet excludes it; explicit producer consumption evidence is needed.

## Durable response content

Render exactly plan.report_items as the new raw events. The UI must show all of them, including repeated bars. Before output, compare rendered event IDs/values/times against the plan. Then call:

```sh
python reporter_delivery.py seal --packet packet.json --ids displayed-ids.json \
  --quarantine carried-quarantine.json --now "$NOW" --out response-suffix.txt
```

The IDs are plan.receipt.item_ids in parent Packet order, and carried-quarantine is plan.quarantine_receipts. Append the generated TWO LINES verbatim OUTSIDE the visualization in the actual final response:

- SignalEvidence-v1: SHA256 plus base64/zlib of the COMPLETE validated delivery fields, exact displayed IDs, and separately labelled unconfirmed claims.
- Receipt: the plain machine-readable receipt covering exactly displayed raw events.

Do not abbreviate, regenerate by hand, replace with a link, or put these only inside HTML. The compact payload contains the full evidence, not only a hash or IDs. A later run must decode and validate it from an actually read prior automation result. Generating it locally does not acknowledge delivery. `seal` must run after complete rendering checks; absent/failed rendering requires the full Markdown fallback, not a success claim.

A missing/corrupt/truncated manifest is an operational failure, never permission to repeat all pending events. A bare legacy receipt is only a quarantine claim. A captured current-run response or retry cannot acknowledge itself. The helper's bounded decoding rejects corrupt or oversized payloads and inconsistent values/timestamps/order.

No post-display callback or atomic multi-reporter lock is introduced. This is complete-prior-response evidence under the existing contract, not proof the user read the report, and not an exactly-once guarantee.

## Report content: preserve existing semantics

Begin with 新信号（N）. No generic title, integrity-success boilerplate, recovered operational history, repeated legends, live-price disclaimer, or no-overdue prose. Signals are recorded values, not live quotes. Separate recent events and overdue catch-up by their event times, not Packet as_of. Sort 1W, 1D, 4H, 30m, newest first within TF; use ticker only, 抄底/卖出, value, and confirmed time. Do not publish lifecycle stages or higher-TF duplicate/internal records. Never omit a raw event.

Keep the report inside the recurring ChatGPT response. Read the available work-visualize:visualize skill when available and use only its supported inline content reference. Create one self-contained frozen-scan file with a unique dated name under /workspace; embed this run's verified data. No prior file, network fetch, browser storage, or new Site. The first view shows EVERY new signal, red 抄底 and green 卖出 text (not colored dots); no new event hidden behind a tab. Show Packet as_of.

Place 近期信号 · 当前结构 behind an expandable section or 当前结构 tab. Embed ALL watch_context.rows, not affected_states or a five-ticker limit. Enrollment remains any confirmed 4H/1D/1W signal within 14 calendar days OR at least three distinct 30m Bottom bars within 48 hours; deduplicate ticker/TF/bar/direction and merge enrollment reasons. These are display windows, not validity/expiry rules. One ticker block, four TF cells. Anchor to the highest signal TF INSIDE the window and its newest signal, never imported historical highest-TF labels. Group by anchor market-local BAR date, newest dates first; within date highest TF then newest confirmation. Exactly one table per date, with Ticker | 1W | 1D | 4H | 30m. Bold the anchor signal/date.

First cell line is cells[tf].structure. Full price > entire Blue > entire Yellow > E200 is bull; exact reverse is bear. Otherwise retain the exact ordered ladder with price dot, repeated blue/yellow band boundaries and E200; no 顶/底 labels, invented weak/mixed/flat scores, or collapsed overlapping bands. Recent signals are SECOND lines in the SAME cell, latest per TF/direction within 14 days; both directions on separate lines, newest first. Color cannot replace textual direction. Never use LaTeX/KaTeX or raw HTML in ordinary Markdown.

Provide 本轮相关 / 全部 and market filters; all eligible context remains embedded when filtered out. Selected details must match the active filter. Distinguish an empty filter result from missing data. All TF snapshots are independently latest confirmed native bars, not signal-event geometry. Current week excluded until closed. Only freshness=current can be called current; stale=旧 MM/DD, unverified=待核, missing=缺. Before declaring missing, read the four exact Scan Status rows and parse Current Structure. Use packet cell.stream / row.instrument_id: Stream is instrument_id:TF, never display ticker:TF or provider symbol:TF. BTC maps to BTCUSD; preserve 0100 leading zeros. Use newer valid source snapshots with their own timestamps/source/freshness without writing state. Freshness Unverifiable/verification disabled means unavailable close/freshness policy, not missing structure or indicator parity.

If the historical context window is incomplete, label coverage incomplete and supplement read-only from configured Signal History and Scan Status after schema reads and complete pagination. Historical queries are for context ONLY, never new delivery IDs. No unresolved TradingView-parity boilerplate.

Use each instrument's configured IANA market_timezone and DST. US equities/indices/ETFs America/New_York; never add a second/user-local clock. Unknown zone stays unknown. Show timezone once per market section and retain exact bar_at/confirmed_at/timezone in audit details. Visible times less than seven days old use Chinese relative labels (刚刚/N分钟前/N小时前/N天前), calculated against the frozen report generation time, NOT a ticking browser clock. At seven days or older use MM/DD, including year if different. Structure main detail has one compact confirmed_at-relative label; missing/future/invalid stays 待核/缺, never silently substitute bar_at or label future 刚刚. Keep bar-date grouping and chronological sorting exact internally.

Any 变化 claim requires two actually retrieved comparable snapshots for the same instrument/TF with compatible definitions and both times. Otherwise show 结构变化：未核验 and current state separately, not 0 reversals/unchanged/repaired. Never use an old conversational summary as a verified baseline.

Finish with concise, scannable evidence-based structural synthesis: important higher-TF new signals versus current structure, shared phenomena using representative tickers. Separate state from change; signal order does not prove upward risk transmission; old windowed signals are not automatically valid or resonant. Yield direction means yield, not bond price. No per-ticker prose repetition, invented slopes/compression/crossover timing, or generic next-step boilerplate. One necessary 数据 line for material data issues, separate from tool/setup/write errors. Remaining batch count when nonzero once; distinguish quarantined items from genuinely unreported items.

If interactive tools are unavailable or creation fails, show 交互视图不可用 once and deliver the COMPLETE Markdown fallback: new-signal table Ticker | TF | 信号 | 信号值 | 确认时间; context per date with a structure row and following blank-ticker signal row (third for opposite direction). Omit unsupported colors; do not substitute dots, raw HTML, math wrappers, or a broken path. Do not duplicate the full matrix in Markdown when interactive rendering succeeds. Always retain the complete machine evidence outside the UI.

If no unreported signals and no new operational change remain, stay silent; no 新信号（0） panel and no new Receipt. Routine unchanged quarantine/data gaps are not recurring new operational events.

## Verification and rollout

Run `python -m unittest discover -s tests -p 'test_reporter_delivery.py' -v`.

Initial local verification on Python 3.13.5: 44 passing tests. These include cold-process recovery with no HTML, 14 repeated items, 14+10 subset delivery, 24 uncertain+2 new, union retention, corruption/truncation, current-run/retry rejection, exact Markdown readback and one-retry limit, and recovery across repeated acknowledgement failures. Live delivery fields were read separately and replayed read-only: all 24 old items withheld as unconfirmed, zero automatic rebroadcasts, zero fabricated acknowledgements. Private event values are not committed in fixtures.

The reporter helper is independent of scanner, indicator, and scheduling code. Pin the reviewed commit and helper SHA-256 in the existing task before enabling this protocol. Do not claim a scheduled production cycle was observed just because unit tests passed; report that separately. The live legacy outbox stays unconfirmed until genuine evidence or an authorized re-delivery resolves it.
