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

Theme evidence aggregates Bottom and Sell separately over an explicitly labelled
rolling timestamp window (default 72 hours, NOT claimed to be three trading
sessions). It provides event density, unique tickers, explicitly mapped independent
exposures, chronological participation, healthy members and failing members.
The compact reporter Packet includes aggregate counts, not full chronology or
member lists. Unmapped exposures remain unavailable. It never infers fading solely from fewer
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
5. After successful delivery, the reporter writes the packet's delivery
   receipt to Delivered IDs ONLY. Scanner consumes that receipt on the next
   run. It never overwrites Delivered IDs. Reporter never writes Engine State.

Delivery is at-least-once: a crash between report delivery and receipt can cause a
repeat. It cannot be claimed exactly-once without a delivery-system transaction.
The standalone `state_cli ack` is for maintenance only under scanner serialization;
ordinary ChatGPT reporting uses the separate Delivered IDs receipt property.

Packets carry per-timeframe timestamps, pre-rendered ladders, availability and
indicator validation. Numeric snapshots remain in Engine State. An unsuccessful scanner run does not publish a fresh packet;
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
A fresh production Packet must be verified before adopting the updated prompt.
The six-hour schedule is unchanged.

## Coverage and delivery fields

Scan Status exposes Expected Through, Freshness Status and Missing Count alongside
Continuous Through (durably processed bar-open time) and Last Attempt. These three
new columns are optional for backward-compatible rollout. Unverifiable has no
expected time or missing count; Success means the scan/write completed, not that
coverage is verified. Current Structure retains detailed freshness evidence.

Packet as_of is generation time. packet_id identifies its sorted item-ID set,
not a snapshot hash; coverage and current structures can change with the same set.
coverage_snapshot records per-stream checked_at and expected/actual bar opens.
Coverage checks apply after the checkpoint, not to all historical provider bars.
New transitions retain bar_at, confirmed_at and detected_at; operational items
have no bar/confirmation clock. Existing items retain their original IDs and
missing historical metadata is not invented.

Delivered IDs accepts the legacy JSON array/strict CSV or the new receipt object:
{packet_id, item_ids, delivered_at}. The reporter verifies a complete prior
report; precise delivery time may be explicitly unknown under the protocol below.
Python persists delivered_at separately from receipt_confirmed_at in Engine
State's delivered ledger. Legacy receipts have unknown delivery time and do not
manufacture Raw Event reporting timestamps.
RAW_SIGNAL events with a source_event_id are marked Reported/Surfaced together before
the corresponding pending item is acknowledged. Failed writes leave it pending
and do not stop market-state processing. Replay preserves the first delivery.
Lifecycle changes are separate report items, not delivery of the original raw
signal. The only evidence of receipt is the reporter's verified prior final
message; this is still not a platform transaction or exactly-once guarantee.

## Bounded delivery and unknown message times (2026-10-07)

Packet selects an oldest-first prefix of at most 25 pending items and fits the
complete payload, including coverage and operational diagnostics, within 12,000
characters. `batch` gives total/included/remaining counts. `report_items` supplies
one deterministic line per selected ID. Unselected items remain in Engine State;
only acknowledged IDs leave pending. A retry can refresh structures but never
consumes an item. A single item that cannot fit fails explicitly. This replaces
the previous unbounded all-pending Packet; no date cutoff or automatic deletion
is introduced. Theme evidence still uses its own labelled rolling window.

### Compact reporter readback (Packet version 2)

The earlier 175,000-character bound addressed Notion publication, not the smaller
ChatGPT connector response budget. Version 2 uses a conservative 12,000-character
read budget. This is not a measured connector limit or a guarantee of complete
tool output; an actual scheduled-task read/report/receipt round trip is required.

`report_items` is the sole event list, with id, kind and the complete deterministic
line. Duplicate raw_signals/new_moves/state_changes arrays are removed. The full
pending records, including source event IDs and all original event metadata,
remain in Engine State. Only acknowledged IDs leave pending; reducing the batch
never truncates an item line or consumes an unreported event.

Current structure context carries timestamp, confirmed_at, value_unit and the
same deterministic ladder rendered from the confirmed snapshot; full numeric
snapshots are omitted. Coverage retains status, checked_at, expected_latest,
actual_latest and missing_count. Theme context retains counts, highest timeframe,
exposure breadth and its timestamp window, without long member lists or chronology.
The Packet includes at most five affected ticker contexts, five data-gap records,
five operational-error records and two themes. `context_omitted` explicitly counts
additional contextual records. These are not omitted report items, and diagnostics
must not be presented as exhaustive when the corresponding count is nonzero.

`packet_end` is the final JSON field and repeats packet_id. Before producing a
Receipt, the reporter must read complete delivery fields, verify this marker,
match unique report_items IDs to acknowledgement_ids in order, and check their
count against batch.included. A marker or count alone proves no item coverage.
Truncated delivery fields still fail closed. Missing optional structure context
is labelled unavailable and does not block a complete signal-only report. The
marker is a read boundary, not a snapshot hash or platform delivery proof.

Receipt objects may now explicitly contain `delivered_at: null` together with
`delivery_evidence: "prior_complete_report"`. The reporter must have actually
read the complete earlier report; the flag is an attestation, not a platform
proof. A real `report_ref` is optional and stored in the delivery ledger. Missing
history or incomplete coverage still cannot be acknowledged. A precise message
time is optional; `receipt_confirmed_at` remains a distinct scanner timestamp.
This supersedes the earlier requirement that every new receipt have a known
message timestamp. Legacy ID lists remain readable for migration.

Only acknowledged RAW_SIGNAL items update the corresponding source event's
Reported/Surfaced fields. Unknown delivery time leaves Reported At empty, while
Report ID identifies the verified batch. Lifecycle/context items do not mark the
raw signal reported. Retries preserve the first recorded delivery, including an
explicitly unknown time. A failed raw-event write retains the ticker's pending
items for reconciliation on the next scan.

Public run summaries expose only safe Packet counts, character budget, failure
phase/code/type and HTTP status where available. Private payloads and request
paths stay out of the public summary. Production recovery also requires applying
the updated reporter prompt and validating actual report/receipt consumption;
a local test or a green scanner alone is not an end-to-end delivery check.
