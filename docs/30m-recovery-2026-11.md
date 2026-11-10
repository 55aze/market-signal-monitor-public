# November 30m stream recovery

## Observed failures and evidence

Production head `3d9f8324d310c33cbc96819aaae9a2bcca741cb1` rejected four TVC
Treasury-yield 30m feeds at `2026-11-02T00:00:00Z` and FX_IDC:JPYUSD at
`2026-10-23T21:30:00Z`. LITE and SPCX transient fetch errors had already recovered
by the November 9 16:07 UTC production scan; neither identity nor retries changed.

Read-only runs 42653532598 and 42654757461 fetched 2,200 actual native timestamps
per stream without credentials or Notion access. The latter also captured
allowlisted symbol_resolved metadata. Its artifact 13691198001 has SHA256
`b472725bde3ef3559448901b0a114d2444c259f563ad56d3838ccbf4941925c5`.
These are timestamp/session tests, not TradingView signal parity certification.

## Corrections

- Treasury 30m: native metadata publishes `1900-1730`, America/New_York. The
  previous 20:00 opening rejected the observed 00:00 UTC winter bars (19:00 ET).
  Restore the 19:00 session opening. Preserve the existing explicitly recorded
  optional reopen-prefix handling for summer sessions whose first bar is later.
  Interior holes, wholly absent sessions, off-grid bars and unfinished bars are
  not waived. The 4H/1D/1W conventions and SIFMA holidays remain unchanged.
- JPYUSD: metadata publishes `2200-2200`, Etc/UTC. The independent native
  September/October captures ended Fridays at the 21:00 opening until October
  16, but include the 21:30 opening on October 23, October 30 and November 6.
  Use a dated 30m close-grid change from October 23: 21:30 opens close at 22:00.
  Preserve the earlier 21:30 close expectation; do not invent old missing bars.
  Recompute each week's bounds during gap checks so crossing the effective date
  uses the right grid rather than carrying the old closing time forward.

## Unresolved DXY gap (deliberately not bypassed)

TVC:DXY still lacks `2026-10-25T22:00:00Z` and `2026-10-25T22:30:00Z` in two
independent native captures. Its first returned bar is 23:00. The retrieved
metadata does not prove an exception for that date. Preserve Gap and the original
checkpoint: absence alone is not evidence of a later legitimate session open.
Do not fill synthetic bars, switch instruments, move checkpoints, or generalize
Treasury's optional-prefix policy to DXY. Recovery needs the actual missing bars
or provider-specific evidence of that session's different open.

## Scope and verification

No signal formula, OHLC value, instrument identity, interval, activation date,
external scan schedule, reporter prompt, receipt, or Notion row is manually changed.
The ordinary serialized scanner remains the sole data writer and resumes from its
existing checkpoints. Repaired timestamp replays reach November 9 15:30 UTC for
all four yields and JPYUSD, without gaps after those checkpoints. Test fixtures
also require genuine removed interior bars and missing last bars to remain errors.
A deployed fix is not a claim that subsequent live production writes succeeded;
that must be checked against the actual run and persisted Notion timestamps.
