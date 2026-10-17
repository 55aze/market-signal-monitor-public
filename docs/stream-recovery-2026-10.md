# Structure freshness recovery

The 2026-10-16 report combined three different issues:

- BTC is displayed as BTC but scanned under BTCUSD:TF; 0100 uses Stream
  0100:TF and provider Symbol 100. Both already had four populated Current
  Structure snapshots in Scan Status. The reporter must use exact stream IDs.
- DXY (TVC) and XAUUSD (OANDA) had snapshots but no close policy, so freshness
  verification was disabled. Native TradingView bars were fetched on October 16
  (1,000 bars per instrument/TF) to verify the regular-week label grid.
- US02Y/05Y/10Y/30Y incorrectly used the NYSE holiday calendar. October 12 was
  a full bond-market holiday. Missing expectations (43 30m, six 4H, one daily)
  therefore blocked durable progress even after later bars returned.

Treasury sessions now use SIFMA_US full-day holidays with the existing audited
TVC session anchors. Cash-market early-close times are not silently substituted
for the TVC feed's bar grid. Source:
https://www.sifma.org/news/press-releases/sifma-fixed-income-market-close-recommendations-in-the-u-s-the-u-k-and-japan-for-u-s-columbus-day-and-japan-health-and-sports-day-holidays-2026

Regular-week anchors below use America/New_York (DST aware). They describe TV
bar labels, not necessarily the broker's executable quote session:

| Feed | TF | Session start | Session end |
| --- | --- | --- | --- |
| DXY | 30m | Sunday 18:00; Mon–Thu 19:30 | Mon–Thu 18:30; Fri 17:00 |
| DXY | 4H/1D | Sunday 17:00; Mon–Thu 19:00 | Mon–Thu 19:00; Fri 17:00 |
| XAUUSD | 30m | Sun–Thu 18:00 | Next day 17:00 |
| XAUUSD | 4H/1D | Sun–Thu 17:00 label | Next day 17:00 |
| Both | 1W | Sunday 17:00 label | Friday 17:00 |

The observed XAUUSD 18:00 half-hour label contains a later quote-session open;
OANDA lists XAU/USD quotes at 18:05–16:59 New York. Do not replace native labels
with quote timestamps: https://www.oanda.com/bvi-en/cfds/hours-of-operation/

These two new regular-week grids do not certify exceptional holiday sessions.
Missing expected bars remain explicit gaps; they are never dismissed merely
because every tenor/feed is missing them. Historical nonstandard labels retain
successor-bar evidence; an unrecognized latest label fails closed.

Replay of actual October 8–16 bars for DXY, XAUUSD and US02Y passes all four TF
freshness checks after these corrections. Regression tests also remove a real
interior bar and require a gap, and cover Friday close, weekend, DST, identity
mapping and lifecycle freeze isolation.

Healthy native-TF snapshots now refresh before lifecycle replay guards. A
missing sibling TF or late-signal replay requirement still freezes lifecycle
transitions, but does not freeze healthy descriptive structure snapshots.
Existing events/checkpoints/receipts are not deleted or manually advanced.
