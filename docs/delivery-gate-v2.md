# Trading Signal Watch delivery gate v2

## Problem reproduced on 2026-10-19

The reporter can only persist acknowledgement evidence for a report on a later run; there is no atomic post-display callback. Meanwhile the scanner continues to publish `Packet` from its durable pending outbox. Therefore a valid Packet can temporarily contain both:

- IDs already shown in the previous complete report, and
- genuinely new IDs discovered after that report.

The old reporter rule treated any partial overlap between `Delivered IDs` and `Packet.acknowledgement_ids` as `ACK_PENDING`. That is safe but unnecessarily blocking: new signals wait for another scanner cycle even though the full parent Packet and the durable acknowledgement set are both known exactly.

This is not a scanner subtraction bug. `scanner.py` already reads `Delivered IDs`, calls `acknowledge(...)`, and only then builds the next Packet. The race exists because the acknowledgement for a displayed response is necessarily persisted later than the display itself.

## Delivery rule

The reporter must first validate the complete parent Packet, before suppressing anything:

1. `version == 2`.
2. `packet_end == packet_id`.
3. Every `report_items` line is fully readable.
4. `report_items` IDs are unique and exactly equal `acknowledgement_ids` in order.
5. `len(report_items) == batch.included`.
6. The persisted `Delivered IDs` receipt is independently valid under `delivery_receipt(...)`.

Only after those checks, define:

```text
covered_ids = durable Delivered IDs
              union IDs from an actually readable complete prior report

undelivered_items = [
    item for item in Packet.report_items
    if item.id not in covered_ids
]
```

A partial overlap is **not** an `ACK_PENDING` condition. The reporter may display every `undelivered_item` because the items come verbatim from a fully validated parent Packet; nothing is reconstructed from history.

## Receipt for a filtered delivery

The visible Receipt covers exactly the uncovered IDs actually displayed. Its `packet_id` is recomputed from that subset using the existing backend identity contract:

```python
identity("packet", sorted(set(uncovered_ids)))
```

Do not reuse the mixed parent Packet's `packet_id` for a subset.

On a later run, if the scanner has not yet consumed older durable receipt IDs, acknowledgement persistence must preserve both generations:

```text
union_ids = persisted_ids union prior_complete_report_ids
packet_id = identity("packet", sorted(union_ids))
```

Write the union receipt and verify exact readback. This prevents a newer acknowledgement from erasing still-unconsumed older evidence.

## Invariants retained

- Never suppress before the complete parent Packet passes integrity checks.
- Never infer missing item lines from history.
- Never acknowledge the current response before it is displayed.
- A malformed/unreadable receipt still fails closed.
- If every Packet ID is already covered, remain silent except for material operational changes.
- If `Delivered IDs` is empty and prior delivery history is unavailable, retain the first-run/`DELIVERY_UNCONFIRMED` protection.
- The scanner remains the owner of durable state and ultimately consumes receipts; this rule only prevents a temporary producer/reporter timing gap from blocking genuinely new delivery.

## Concrete regression case

Observed 2026-10-19 state:

```text
Packet: 14 RAW_SIGNAL IDs
Persisted Delivered IDs: 3 IDs
Overlap: exactly those 3 previously reported IDs
Expected reporter result: 11 new signals, not ACK_PENDING
```

This is the canonical regression case for the gate.