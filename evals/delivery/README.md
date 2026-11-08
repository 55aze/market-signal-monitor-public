# Delivery regression — 2026-10-18

## What changed

Reporter instructions now read persisted Delivered IDs before using chat history to determine new signals. They preserve an unconsumed receipt, reject malformed/incomplete reads, and defer mixed already-reported/new packets until the producer consumes the receipt. No scanner formula or state mutation code changed.

The existing backend was exercised directly: saving a receipt alone does not remove pending items; only acknowledgement consumption does. Until then the producer can expose the same batch. This reproduces a mechanism, not the exact cause of a historical production incident.

No history + empty receipt is ambiguous. The proposed policy emits DELIVERY_UNCONFIRMED unless first delivery is explicitly authorized. This trades automatic availability for avoiding duplicate delivery. It is not an exactly-once system. Recovery requires actual evidence, explicit replay authorization, or a future durable prepublication ledger. Concurrent reporters and interruption after display but before recording remain unsolved.

## Fixed-input LLM comparison

- Frozen live automation prompt: prompt_before.txt.
- Same prompt plus delivery gate: prompt_after.txt.
- 20 synthetic tool-snapshot cases, chosen before model execution; not historical traces.
- Two separate blind model sessions: each processes the same 20 cases, one prompt per session. No expected answers supplied. This is 40 scenario decisions, NOT 40 independent stochastic trials.
- Output is protocol decisions (emit IDs / receipt IDs / receipt writes / status), NOT full rendered reports or actual tool execution.
- Before: 7/20 exact case passes. After: 20/20.
- Some failures concern revised safe-failure policy/status naming, not duplicate output. scores.json preserves each criterion rather than treating all failures as duplication.
- 19 existing and added backend tests passed.
- Do not infer a general reliability rate or production delivery success from these results.

Example: case 2 has a complete persisted receipt for both items, no readable prior chat, and unchanged Packet. Before emits both IDs again; after emits none. Case 4 has acknowledged A/B plus new C: after returns ACK_PENDING instead of issuing a subset receipt with the original batch hash. Case 5 models the following scan having consumed A/B: C is reported.

## Top-down acceptance requirements

| User outcome | Observable check | This run |
|---|---|---|
| Old signals never become new merely because chat/as_of changes | emitted IDs exclude durable verified IDs | Covered by simulated cases |
| New signals remain deliverable | after receipt consumption, only new IDs emitted | Case 5 + actual state/packet replay |
| No false delivery acknowledgement | no current response acknowledged; receipt exactly matches delivered batch; no overwrite of unconsumed evidence | Decision-level checks; no real display proof |
| Failures remain visible and recoverable | unreadable/invalid data returns explicit error; pending retained; mixed batch deferred | Simulated errors + backend tests |
| Facts stay faithful | ticker/TF/direction/value/time match actual input | NOT evaluated here |
| Interpretation stays within evidence | current snapshot != transition; order != causation | NOT evaluated here |
| Delivery works across actual runs | real output, persisted receipt/readback, next producer scan, next reporter all linked | NOT evaluated here |

These are separate gates. Do not average fact corruption, duplication, or false acknowledgement into an overall quality score. Safe deferral is not successful delivery; unresolved deferrals must stay visible.

## Re-run

```sh
PYTHONPATH=src python evals/delivery/build_cases.py
PYTHONPATH=src:tests python -m unittest test_packet_batches test_state_store test_receipt_replay_regression -q
python evals/delivery/score.py
```

The last command rescores saved LLM outputs; it does NOT invoke a model. To repeat generation, supply only one prompt and cases.json to a fresh model session, save untouched outputs under a new name, and score independently. Preserve prompt, inputs, output, and criterion results together.
