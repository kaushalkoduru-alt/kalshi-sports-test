# Pre-registration v1: sports gap test (Kalshi and Polymarket US vs Pinnacle)

Written 2026-11-09 ~18:25 UTC, before any data was collected: no Pinnacle odds, no gaps, no matches. The only
venue data seen was the format of one Kalshi NBA game market.
Kaushal's choices: free odds feed, 2-week paper test, both venues.

## Question
When Kalshi or Polymarket US sells a team's win for less than Pinnacle's fair price by more than the fees, is the
gap a real mispricing (Pinnacle doesn't move toward the venue, and the bets win money), or just Pinnacle being slow?

## Data (paper only: no orders anywhere)
- Pinnacle odds from pinnapi.com, free tier (100 requests a day), prematch fixtures per sport, full-game moneyline
  (period 0), decimal odds.
- Kalshi public API: game-winner markets and order books. Polymarket US public API: game-winner markets and best
  bid/ask.

## Universe
1. Sports: basketball, hockey, American football, soccer (every league that both Pinnacle and the venue list).
   Tennis, MMA and esports are out (retirement, no-contest and map rules differ between venues).
2. A venue game counts only if it pairs with exactly one Pinnacle event: both teams match by name (normalized, with
   alias tables) and Pinnacle's start time is within 18 hours of the venue's listed game time. Ambiguous or
   one-team matches are skipped and logged. Pinnacle special rows (parent_id set) are ignored.
3. Pre-game only: a comparison needs the game to start at least 10 minutes after the Pinnacle snapshot.
4. Soccer is 3-way (home, draw, away); the other sports are 2-way and include overtime. If Pinnacle's period-0
   moneyline for a basketball, hockey or football game has a draw price, that game is skipped.
5. A bet on a game later found to be mismatched (wrong teams or wrong game) is removed and listed in the report.

## Fair value
Pinnacle decimal odds to implied probability 1/odds, divided by the sum over the game's outcomes (proportional
de-vig). Games with a missing or invalid price on any outcome are skipped.

## Snapshots and budget
- Pinnacle is polled per sport. A snapshot is due when (a) a matched game in that sport starts within 35 minutes and
  the sport has no snapshot in the last 30 minutes, or (b) the sport's last snapshot is 3+ hours old and it has a
  matched game in the next 24 hours. Pre-start snapshots go first when the budget is tight.
- Hard cap: 90 pinnapi requests per rolling 24 hours. Skipped snapshots are counted and reported.
- Venue prices are fetched right after each Pinnacle snapshot for the matched games. A comparison is valid only if
  the venue price was fetched within 120 seconds of the Pinnacle snapshot and the venue market is open.

## Entry rule (paper bet)
- Venue ask = best ask for "this outcome wins" (Kalshi: 1 - best NO bid; Polymarket US: best ask of that outcome's
  market), with at least 10 contracts at that price.
- Taker fee per contract at price p: Kalshi 0.07 x p x (1 - p); Polymarket US 0.05 x p x (1 - p) (sports rate).
- Edge = Pinnacle fair - ask - fee. Paper bet when edge >= 1 cent.
- One bet per (game, venue, outcome): the first snapshot where the rule fires. Size: min(100, contracts at the best
  ask) at the ask. P&L charges the fee rounded up to the cent per bet.

## Metrics
- Primary, CLV vs Pinnacle's close: per bet, contracts x (Pinnacle fair at close - ask) - fee; summed and divided by
  dollars risked (contracts x ask). Close = the sport's last Pinnacle snapshot before the game starts, taken no
  earlier than 90 minutes before the start. A bet is in the primary sample only if its close was taken at least
  20 minutes after its entry snapshot. 95% CI by cluster bootstrap over games (10,000 resamples).
- Secondary: settlement P&L per $1 after fees (all bets, each venue's own result; voided markets excluded),
  expected P&L at entry, venue convergence (venue mid at start - entry ask), edge buckets (1-2c, 2-3c, 3c+), by
  sport, match counts, budget shortfalls.

## Verdict, separately for Kalshi and Polymarket US, after 14 full UTC days
- PASS: 60+ primary bets, CLV 95% CI lower bound > 0, CLV positive in both weeks, and settlement P&L per $1 >= 0.
  Next step: a small live test, with its own rules written first.
- REJECT: CLV 95% CI upper bound < 0 (Pinnacle moves toward the venue price, so the gaps are a slow Pinnacle, not
  mispricings), or settlement P&L 95% CI upper bound < 0.
- TOO FEW: under 30 primary bets (the gaps are too rare to matter at this sampling).
- INCONCLUSIVE: anything else.

## Window
14 full UTC days starting the first full UTC day after the final code is deployed and verified (target 2026-11-11
to 2026-11-24, readout Nov 25). During the window only plumbing fixes are allowed (matching, parsing, crashes, rate
limits); each is logged with its time. The rules above don't change.
