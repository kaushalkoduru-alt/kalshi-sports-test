# kalshi-sports-test

Paper test: when Kalshi or Polymarket US sells a team's win for less than Pinnacle's fair price (after fees), is that a
real mispricing? The rules, the pass bar and every number in `sportsbot/config.py` `PRE` were written down before any
data was collected: see [PREREG.md](PREREG.md). No orders are ever placed anywhere.

- **Pinnacle** odds from pinnapi.com (free tier, 100 requests a day; the bot uses at most 90 per rolling 24h).
- **Kalshi** and **Polymarket US** public APIs (no account needed for paper).
- Sports: basketball, hockey, American football, soccer. Moneyline only.

## Deploy (Railway)
Service from this repo, a Volume mounted at `/data`, Variables: `PINNAPI_KEY` (the free key), `READ_TOKEN`,
optional `PROBE=1` (collect and show samples, no bets) and `WINDOW_START=YYYY-MM-DD` once the code is verified.

## Endpoints (all but /health need `?t=READ_TOKEN`)
- `GET /health`
- `GET /status` budget, snapshots, listing and matching counts, bets, recent events
- `GET /report` PREREG metrics per venue and the verdict
- `GET /matches` every venue game with its Pinnacle match (for audits)
- `GET /probe` and `/probe?k=...` raw samples (probe mode)
- `GET /export?table=bets|quotes|pin_snaps|pin_events|pin_calls|games|events|meta`

## Checks
`python -m sportsbot selftest` runs the offline checks (de-vig, fees, matching, scheduler and budget, entry rule,
closes, settlement, report and verdict).
