# Trading Signal Watch deterministic migration

Ticker lifecycle is now an opt-in part of the native-bar scanner. The existing
external scan cadence and ChatGPT's six-hour report cadence are unchanged.

## Rules implemented

- Replay confirmed native bars in confirmation-time order, never continuously
  resample across sessions. Acceptance is STRICTLY above Blue Upper for Bottom,
  below Blue Lower for Sell: 30m=5, 4H=4, 1D=3, 1W=3 distinct closes.
- Import the existing state/checkpoint without reconstructing historical stages.
  The six private baseline snapshots are NOT historical OHLC replay evidence.
- Conservative first retest: after Qualified, the first CLOSE inside the Blue
  band, including its boundaries, becomes Bottom Opportunity. A wick alone does
  not qualify; a close below Blue Lower is Weakening. The next origin close still
  beyond the failed boundary becomes Failed. Recovery beyond the opposite edge
  rebuilds acceptance from Developing. Intermediate inside closes leave Weakening.
- After a retest, re-establishing the same number of accepted origin closes above
  Blue enters Trend silently. A later close inside Blue can reopen Opportunity.
  No arbitrary percentage extension is used. Sell can qualify and deteriorate but never
  automatically creates a mirrored short Opportunity.
- New higher-timeframe opposite signals terminate the prior setup and start a
  new one, preserving the old success/failure history. Lower-TF signals remain
  in lineage. A lone new 30m signal does not create an active setup.
- Missing/invalid bands, absent origin checkpoint, fetch failures and stale tails
  cannot manufacture confirmation or setup failure. Explicit uncertainty is retained.
- State and pending notification IDs are one durable Notion property update.
  Ambiguous writes reconcile by replay/loading persisted IDs. Never truncate
  the transition history or outbox when storage reaches its bounded limit.

## Deliberately not invented

The existing lineage text lacks exact historical effective-until data. Highest TF
fields retain the imported known evidence and new observations; they are NOT a
validated expiry engine. Signal expiry, repeated-30m cluster enrollment and geometric
compression need explicit validated policies.
No automatic expiry is applied. A successful setup is not expired just because its
original raw signal aged. Imported theme support is context, never a stage trigger.

Theme reporting aggregates Bottom and Sell separately over an explicitly labelled
rolling timestamp window (default 72 hours, NOT claimed to be three trading
sessions). It provides event density, unique tickers, explicitly mapped independent
exposures, chronological participation, healthy members and failing members.
Unmapped exposures remain unavailable. It never infers fading solely from fewer
Bottoms or starts a parallel theme lifecycle state machine.

## Private configuration / cutover

Before enabling, add `Engine State` (rich_text) to the EXISTING Active Ticker States
source. Keep one state row per configured Notion ticker. Create one private report
row with `Packet` and `Delivered IDs` rich_text properties. Keep IDs/config/secrets
out of this public repo. Configure in MONITOR_CONFIG_JSON:

```json
{
  "ticker_engine": {
    "enabled": true,
    "state_data_source": "PRIVATE_SOURCE_ID",
    "report_page": "PRIVATE_REPORT_PAGE_ID",
    "theme_window_hours": 72,
    "policy": {
      "acceptance": {"30m": 5, "4H": 4, "1D": 3, "1W": 3}
    }
  },
  "theme_membership": {"SPX": ["Broad US"], "SPY": ["Broad US"]},
  "exposure_groups": {"SPX": "US-large-cap", "SPY": "US-large-cap"}
}
```

1. Export current baseline and retain private backups. Validate replay against
   actual historical native bars before allowing production writes. Snapshot-only
   regression cannot establish historical transition parity.
2. Stop the old ChatGPT task's lifecycle writes before enabling Python. There must
   be exactly one state owner; scanner workflow already serializes writers.
3. Enable Python configuration and verify state + packet write/readback.
4. Point ChatGPT at the private report row and use the adjacent thin prompt. Keep
   its six-hour cadence. It reads Packet only, not the full Engine State payload.
5. After successful delivery, the reporter writes the packet's acknowledgement_ids
   JSON array to Delivered IDs ONLY. Scanner consumes that receipt on the next
   run. It never overwrites Delivered IDs. Reporter never writes Engine State.

Delivery is at-least-once: a crash between report delivery and receipt can cause a
repeat. It cannot be claimed exactly-once without a delivery-system transaction.
The standalone `state_cli ack` is for maintenance only under scanner serialization;
ordinary ChatGPT reporting uses the separate Delivered IDs receipt property.

Packets carry per-timeframe timestamps, numeric bands/EMA values, availability and
indicator validation. An unsuccessful scanner run does not publish a fresh packet;
the thin reporter must flag stale packets rather than silently use old evidence.
Pending events remain until acknowledged, including events older than the rolling
theme window. Publish failures are retried without losing the durable outbox.


### Failure isolation and delivery limits

Per-stream failures include a `kind` identifying provider fetch, bar validation,
confirmed-bar gaps, calculation or Notion status access. Public CI summaries expose
aggregate categories, never raw private error payloads. Provider gaps preserve
checkpoints and freeze the affected ticker, while healthy tickers continue. A
state save failure is isolated to that ticker. Packets expose partial coverage and
operational errors; shared raw event/checkpoint write failures still block state
advancement and packet publication.

The ChatGPT task must not acknowledge its current response before delivery. A
later run may acknowledge exact IDs only from a visible, complete prior final
report. This is a conservative prompt guard, not a platform delivery callback or
a guarantee of exactly-once delivery; unavailable history can cause duplicates.
The task remains paused until a fresh production Packet is verified. The six-hour
schedule is unchanged.
