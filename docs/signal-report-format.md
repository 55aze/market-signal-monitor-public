# Signal report presentation contract

Use Packet `presentation=signals-and-current-structure-v1`. Packet remains version 2 with the existing complete-read and receipt integrity checks. `report_items` and `acknowledgement_ids` contain RAW_SIGNAL only. Lifecycle, higher-TF duplicate notifications and operational history remain stored in Engine State; their exclusion is not acknowledgement or delivery. Do not publish Qualified/Developing/Opportunity/Trend classifications. Keep existing instrument coverage and scheduling.

PART 1 — 新信号
- Report every outstanding raw signal, with ticker only, TF, 抄底/卖出, price/value, confirmed market-local date/time. Sort 1W, 1D, 4H, 30m; newest first within each TF. Preserve each event, including repeated same-direction bars. Clearly separate overdue catch-up from recently occurring signals; packet generation time is not signal time.
- No lifecycle changes, duplicate HIGHER_TF_SIGNAL notifications or operational chatter in this section.

PART 2 — 近期信号与最新结构
- Use all watch_context.rows, not affected_states and not the five-context legacy limit. Selection: any confirmed 4H/1D/1W signal within 14 calendar days OR at least 3 distinct 30m Bottom bars within 48 hours. Deduplicate same ticker/TF/bar/direction. Merge enrollment reasons, one row per ticker. These are report inclusion windows, not signal validity/expiry claims.
- For each ticker anchor to the highest TF with a signal INSIDE the window, then its newest signal at that TF. Group by this anchor's market-local bar date, dates newest first; within a date TF highest first, then newest confirmation first. Never anchor to imported historical highest-TF labels.
- Separate table per date. Columns: Ticker | 1W | 1D | 4H | 30m. Only ticker symbols, no company names and no separate other-signals column.
- FIRST LINE of each TF cell: cells[tf].structure. Full price > Blue > Yellow > EMA200 is `bull`; exact reverse is `bear`. Otherwise concise ladder with ●, blue/yellow, E200, no 顶/底 text. Repeated same-color symbols retain band boundary ordering; never collapse overlapping bands into a false ordering. This classification is not EMA alignment or a lifecycle stage.
- SECOND LINE inside that SAME cell: its recent signal and market-local MM/DD. 抄底 text RED, 卖出 text GREEN. No colored dots or circles. If both directions exist at this TF, show each on its own separate line, newest first. Do not concatenate signals after the structure. Signals list the latest occurrence per TF/direction within 14 days; say this once.
- Use a rendering surface with actual line breaks and colored text. A normal Markdown table can use KaTeX cell content such as $\substack{\text{bull}\\\color{green}{\text{卖出 10/14}}}$ and red for 抄底. Do not rely on literal HTML <br> or style attributes if the surface prints or strips them. A supported inline rendered table is also acceptable. Do not replace color with dots.
- Every TF structure is its latest confirmed native bar snapshot, independently of signal occurrence. Check cell freshness and bar_at/confirmed_at. Only say current when freshness=current; mark stale/unverified/missing per cell. Never reuse signal-event geometry as current geometry. State actual structure cutoff times once, with exceptions per affected cell. Current week is excluded until closed.
- watch_context reflects dated engine-observed history, not imported free text. If a needed historical window is not covered, explicitly label coverage incomplete and supplement from persisted Signal History and Current Structure records, never inferred labels. Historical queries supply part-2 context only and cannot expand part-1 delivery IDs.
- Finish with at most 3 short sentences of structure synthesis: key cross-TF strength/weakness and material differences between signal and structure. No per-ticker prose repetition, invented slopes, compression, crossover timing or generic next-step boilerplate.

TIME AND SCOPE
- All display dates/times use each instrument's own market_timezone (IANA conversion handles DST). US equities/indices/ETFs use America/New_York. Never add Taipei, Shanghai, user-local or a second clock. Show timezone once per market section. For unknown zones say unknown; do not invent local time. Group distinct markets separately when needed, retaining the existing configured universe.
- Price is the recorded signal value, not a live quote. Distinguish bar date, confirmation time, packet as_of and current structure time. Yield signals refer to yields, not bond prices.

DELIVERY AND ERRORS
- Retain complete prior-report evidence/readback safeguards. No current-response acknowledgement. A new-contract Receipt covers exactly the delivered raw report_items. Internal records never go into that Receipt. A legacy packet without this presentation marker may be shown as a transitional signal preview, but do NOT issue a Receipt for a selectively omitted legacy batch; wait for the next producer scan. Complete older reports may still be acknowledged under their original contract.
- Active material data issues get one concise 数据 line, separate from signals; no routine recovered-history dump. Missing context must not be called zero signals. No unresolved TradingView parity boilerplate.
- If no undelivered signals or operational changes, keep the existing silence policy. Remaining batch count, when nonzero, is stated once.
