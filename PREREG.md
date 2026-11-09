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

---

# Amendment v1.1

Written 2026-11-09 ~18:55 UTC, before the window, before any Polymarket US price was compared with Pinnacle, and with
no paper bets (the bot ran in probe mode, which never bets). Found while fitting the parsers to live payloads:

1. Polymarket US's taker fee is coefficient x C x p x (1 - p), and the coefficient depends on the trader's trailing
   30-day taker volume. Since Nov 6, 2026 the tiers are 0.095 (under $10,000), 0.085, 0.075 and 0.0695 (Polymarket US
   fee schedule, docs.polymarket.us/fees), and the API reports feeCoefficient 0.095 on every market. The 0.05 "sports
   rate" in v1 came from an outdated third-party summary. From now on the Polymarket US fee per contract is the
   market's own feeCoefficient x p x (1 - p) (0.095 today: the tier a $100 account would be in); a market that
   doesn't report one is not compared. P&L rounds each bet's fee to the cent with banker's rounding (the venue's
   rule). Kalshi is unchanged (0.07, rounded up per order).
2. Polymarket US lists a 2-way game as one market whose long side is one team and short side the other. "This outcome
   wins" for the short-side team is bought at 1 - the long side's best bid, with that bid's size as the depth. Soccer
   games are three markets (home, away, tie), regulation time, each bought on its long side.
3. Depth comes from the order book (size at the best price); Polymarket US's best bid/offer endpoint reports price
   levels, not size.

Nothing else changes.

---

# Amendment v1.2

Written 2026-11-09 ~23:42 UTC, before the window (target Nov 11 to 24). Since 19:20 UTC the bot has made 13
verification bets under v1.1. They are outside the window, so they never count. I have looked at their entry prices,
fair prices and edges, and at the venue price at the start for 2 of them. I have not looked at any close, CLV or
result.

What I saw: all 13 are on draws or underdogs priced 9c to 21c, with edges of 1.1c to 3.2c. That is where the v1
proportional de-vig is weakest. It takes Pinnacle's margin off each outcome in proportion to its probability, but
bookmakers put more of their margin on longshots (the favourite-longshot bias), so proportional de-vig leaves
longshot fair prices too high. The documentation of the "implied" R package calls it the least accurate of its
methods. Example: Pinnacle 1.26 / 6.6 / 11.5 gives the draw 14.7c proportional but 13.8c with the power method.
Because the close is de-vigged the same way, CLV would carry the same bias, so the test could pass on a de-vig
artifact instead of a mispricing.

Change (it only makes a bet harder to get and the test harder to pass):
1. The fair price of an outcome is the lower of two de-vigs of the same Pinnacle snapshot: proportional (v1) and
   power (fair = (1/odds)^k, one k per game, chosen so the fair prices sum to 1). The entry rule (fair - ask - fee >=
   1c) and the close used for CLV both use it, so an edge counts only if both methods see it.
2. New secondary number, never part of the verdict: CLV with the proportional fair alone, to show the size of the
   de-vig effect.
3. Nothing else changes: universe, matching, snapshots, budget, fees, sizes, metrics, verdict rules, window.
4. The bot runs v1.2 from version 0.3.0 on. The verification bets made under v1.1 stay outside the window.
