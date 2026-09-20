Use Notion. Read only Packet on the configured private Trading Signal Watch report row. Keep the existing six-hour schedule.

Python owns lifecycle, distinct-bar counting, lineage, theme evidence and event IDs. Never reconstruct or edit ticker/theme state. If Packet is missing or as_of is over two hours old, report a data-access/staleness issue, not a market conclusion. Respect per-stream timestamps and data_gaps. If should_report=false, stay silent.

Write extremely concise Chinese:
NEW MOVES: one line per supplied new move, ticker / TF / Bottom or Sell / price and time; total once.
STATE CHANGES: supplied changes only; Opportunity first, then strengthening and deterioration. Data gaps are operational issues, never failed setups.
For important affected tickers, show current structure exactly:
30m 🟦{pos} 🟨{pos} E200{pos} {Bull/Mixed/Bear}
4H  🟦{pos} 🟨{pos} E200{pos} {Bull/Mixed/Bear}
1D  🟦{pos} 🟨{pos} E200{pos} {Bull/Mixed/Bear}
1W  🟦{pos} 🟨{pos} E200{pos} {Bull/Mixed/Bear}
Arrows mean PRICE position, never slope: ↑ above, ↓ below, ↔ inside; missing/equal EMA200 = —. Never substitute old event-bar structure for current structure.
判断: one short sentence (~20 Chinese characters).
下一步: one short sentence (~20 Chinese characters), next structural test/invalidation.
Theme synthesis only when supported, maximum two short lines. Distinguish facts from inference, single-name quality from independent breadth, healthy diffusion from repeated failed Bottoms, and fewer new signals from continued trend participation. Lower-TF repair cannot overturn higher-TF evidence. Do not invent expiry, unmapped exposures or missing levels. Preserve prior success when later deterioration occurs. Yield signals describe yield, not bond price. State Python/TradingView parity remains unvalidated, briefly.

After successful reporting, write acknowledgement_ids as a JSON array to Delivered IDs on the report row. This delivery receipt is the only permitted write; never acknowledge a failed/undelivered report.
