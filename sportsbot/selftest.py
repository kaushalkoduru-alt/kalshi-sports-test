"""Offline checks with fake venues and a fake clock: de-vig, fees, matching, scheduler and budget, entry rule,
closes, primary sample, settlement, report and verdict."""
import json
import os
import tempfile
import time

from . import config as C
from . import match, pinnacle, polyus, report
from .engine import Engine, bankers_cent, ceil_cent, fee_per_contract
from .store import Store

ok_all = True


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def close(a, b, tol=1e-6):
    return a is not None and b is not None and abs(a - b) <= tol


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class FakePin:
    BudgetExhausted = pinnacle.BudgetExhausted
    calls_last_24h = staticmethod(pinnacle.calls_last_24h)
    parse = staticmethod(pinnacle.parse)

    def __init__(self, clock):
        self.clock, self.payload = clock, {}

    def fetch(self, store, sport, reason):
        now = self.clock()
        if pinnacle.calls_last_24h(store, now) >= C.PRE["pin_budget_24h"]:
            raise pinnacle.BudgetExhausted("cap")
        store.x("INSERT INTO pin_calls VALUES (?,?,?,?)", (now, sport, "ok", reason))
        return {"sport_id": C.SPORTS[sport], "events": list(self.payload.get(sport, []))}


def pev(pid, league, home, away, start, oh, oa, od=None):
    ml = {"home": oh, "away": oa}
    if od:
        ml["draw"] = od
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start))
    return {"event_id": pid, "league_name": league, "home": home, "away": away, "starts": iso,
            "periods": {"num_0": {"number": 0, "money_line": ml}}, "parent_id": None}


class FakeVenue:
    def __init__(self, clock, venue):
        self.clock, self.venue, self.listing, self.quotes, self.results, self.stale = clock, venue, [], {}, {}, 0

    def game_series(self):
        return {"KXNBAGAME": "basketball"}

    def open_games(self, series):
        return list(self.listing)

    def games(self, t0, t1):
        return list(self.listing), []

    def books(self, keys):
        out = {}
        for k in keys:
            if k in self.quotes:
                q = dict(self.quotes[k])
                q["ts"] = self.clock() - self.stale
                out[k] = q
        return out

    def result(self, key):
        return self.results.get(key, (None, None))


def main():
    tmp = tempfile.mkdtemp()
    C.PROBE = False

    # ---------------------------------------------------------------- de-vig and fees
    f, why = pinnacle.devig({"home": 1.8, "away": 2.1, "draw": None}, "basketball")
    check("de-vig 2-way: proportional", close(f["home"], (1 / 1.8) / (1 / 1.8 + 1 / 2.1)) and close(sum(f.values()), 1))
    f, why = pinnacle.devig({"home": 2.5, "away": 3.0, "draw": 3.2}, "soccer")
    check("de-vig 3-way soccer sums to 1", close(sum(f.values()), 1) and set(f) == {"home", "away", "draw"})
    f, why = pinnacle.devig({"home": 2.5, "away": 3.0, "draw": 3.2}, "hockey")
    check("draw price on a 2-way sport: game skipped", f is None and "draw" in why)
    f, why = pinnacle.devig({"home": 2.5, "away": 3.0, "draw": None}, "soccer")
    check("soccer without a draw price: skipped", f is None)
    check("Kalshi taker fee at 50c = 1.75c", close(fee_per_contract("kalshi", 0.5), 0.0175))
    check("Polymarket US fee uses the market's coefficient: 0.095 at 80c = 1.52c",
          close(fee_per_contract("polyus", 0.8, 0.095), 0.0152))
    check("Polymarket US market without a coefficient: no fee, no comparison", fee_per_contract("polyus", 0.8) is None)
    check("Kalshi fee rounds up to the cent per bet", close(ceil_cent(1.7501), 1.76) and close(ceil_cent(1.75), 1.75))
    check("Polymarket US fee uses banker's rounding", bankers_cent(0.125) == 0.12 and bankers_cent(0.135) == 0.14
          and bankers_cent(1.7449) == 1.74)

    # ---------------------------------------------------------------- Polymarket US shapes (as seen live, Nov 9)
    def pm_side(long_, desc, name, safe):
        return {"long": long_, "description": desc, "team": {"name": name, "safeName": safe, "alias": desc}}
    nba_ev = {"slug": "nba-cle-cha-2026-11-09", "title": "CLE Cavaliers vs. CHA Hornets", "startTime": "2026-11-10T00:00:00Z",
              "tags": [{"slug": s} for s in ("sports", "games", "nba", "basketball")], "teams": [],
              "markets": [{"slug": "aec-nba-cle-cha-2026-11-09", "active": True, "closed": False, "feeCoefficient": 0.095,
                           "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_MONEYLINE",
                           "marketSides": [pm_side(True, "Cavaliers", "Cleveland Cavaliers", "Cavaliers"),
                                           pm_side(False, "Hornets", "Charlotte Hornets", "Hornets")]}]}
    g, why = polyus.parse_event(nba_ev)
    check("Polymarket US 2-way game: long side = Cavaliers, short side = Hornets",
          g and [o["key"] for o in g["outcomes"]] == ["aec-nba-cle-cha-2026-11-09#long", "aec-nba-cle-cha-2026-11-09#short"]
          and g["teams"] == ["Cleveland Cavaliers", "Charlotte Hornets"] and g["league_code"] == "NBA")
    nhl_ev = dict(nba_ev, tags=[{"slug": s} for s in ("sports", "games", "nhl", "hockey")],
                  markets=[dict(nba_ev["markets"][0], marketSides=[pm_side(True, "Rangers", "Rangers", "New York Rangers"),
                                                                  pm_side(False, "Oilers", "Oilers", "Edmonton Oilers")])])
    g, _ = polyus.parse_event(nhl_ev)
    check("Polymarket US names: the longer one wins ('New York Rangers' over 'Rangers')",
          g and g["teams"] == ["New York Rangers", "Edmonton Oilers"])
    g, _ = polyus.parse_event(dict(nhl_ev, tags=[{"slug": s} for s in ("sports", "games", "nhl")]))
    check("Polymarket US: an 'nhl' tag alone means hockey", g and g["sport"] == "hockey")
    g, why = polyus.parse_event(dict(nba_ev, title="eBattles: Liverpool (A) vs. Chelsea (B)"))
    check("Polymarket US: eBattles / esoccer are skipped", g is None)
    soc = {"slug": "ecu1-tec-man", "title": "x", "startTime": "2026-11-09T20:30:00Z",
           "tags": [{"slug": s} for s in ("sports", "soccer", "ecu1")], "teams": [],
           "markets": [{"slug": f"atc-{k}", "title": t, "active": True, "feeCoefficient": 0.095,
                        "sportsMarketTypeV2": "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME",
                        "marketSides": [pm_side(True, "Yes", n, n) if n else {"long": True, "description": "Yes"},
                                        {"long": False, "description": "No"}]}
                       for k, t, n in (("tec", "CD Tecnico (Reg. Time)", "CD Tecnico Universitario"),
                                       ("draw", "Tie (Reg. Time)", None), ("man", "Manta FC (Reg. Time)", "Manta FC"))]}
    g, why = polyus.parse_event(soc)
    check("Polymarket US soccer: three markets -> home, tie (draw), away", g and [o["kind"] for o in g["outcomes"]]
          == ["team", "draw", "team"] and g["teams"] == ["CD Tecnico Universitario", "Manta FC"])
    g, _ = polyus.parse_event(dict(nba_ev, tags=[{"slug": s} for s in ("sports", "soccer", "uwcl")],
                                   markets=soc["markets"]))
    check("Polymarket US: women's Champions League flagged women's", g and g["women"])
    saved = polyus._get
    polyus._get = lambda path, params=None: {"marketData": {"state": "MARKET_STATE_OPEN",
                                                            "bids": [{"px": {"value": "0.7500"}, "qty": "300"},
                                                                     {"px": {"value": "0.7400"}, "qty": "900"}],
                                                            "offers": [{"px": {"value": "0.7600"}, "qty": "200"},
                                                                       {"px": {"value": "0.7700"}, "qty": "50"}]}}
    q = polyus.books(["m#long", "m#short"])
    polyus._get = saved
    check("Polymarket US quotes: long ask 0.76 (200), short ask = 1 - 0.75 bid = 0.25 (300)",
          q["m#long"]["ask"] == 0.76 and q["m#long"]["depth"] == 200 and q["m#short"]["ask"] == 0.25
          and q["m#short"]["depth"] == 300 and q["m#short"]["bid"] == 0.24)

    # ---------------------------------------------------------------- names
    ns = match.name_score
    check("names: Los Angeles C = Los Angeles Clippers", ns("Los Angeles C", "Los Angeles Clippers") == 1.0)
    check("names: Los Angeles C != Los Angeles Lakers", ns("Los Angeles C", "Los Angeles Lakers") < 0.8)
    check("names: Kent St. = Kent State", ns("Kent St.", "Kent State") == 1.0)
    check("names: Georgia != Georgia Tech", ns("Georgia", "Georgia Tech") < 0.8)
    check("names: Barcelona != Barcelona B", ns("Barcelona", "Barcelona B") == 0.0)
    check("names: Arsenal != Arsenal W", ns("Arsenal", "Arsenal W") == 0.0)
    check("names: Man City = Manchester City", ns("Man City", "Manchester City") == 1.0)
    check("names: Manchester United != Manchester City", ns("Manchester United", "Manchester City") < 0.8)
    check("names: FC Barcelona = Barcelona", ns("FC Barcelona", "Barcelona") == 1.0)
    check("names: a shared generic word alone doesn't match", ns("North Texas", "North Dakota") < 0.8)
    check("names: club words and numbers drop out ('1. FSV Mainz 05' = 'Mainz', 'Calcio Foggia' = 'Foggia')",
          ns("1. FSV Mainz 05", "Mainz") == 1.0 and ns("Calcio Foggia", "Foggia") == 1.0)
    check("names: women's sides never match men's ('Chelsea FC Women', 'Lyon Feminin', 'Man City WFC')",
          ns("Chelsea FC Women", "Chelsea") == 0 and ns("Lyon Feminin", "Lyon") == 0 and ns("Man City WFC", "Man City") == 0)
    check("leagues: WNBL and '(W)' count as women's", match.is_women("Australia - WNBL") and match.is_women("Idaho (W)"))
    check("alias: Kalshi 'New York' (NBA) = Knicks", match.canon("New York", "NBA") == "NBA:NYK")
    check("alias: Kalshi 'New York R' (NHL) = Rangers", match.canon("New York R", "NHL") == "NHL:NYR")
    check("alias: 'New York' in the NHL is ambiguous", match.canon("New York", "NHL") is None)
    check("alias: Clippers / LA Clippers / LAC", match.canon("Clippers", "NBA") == match.canon("LA Clippers", "NBA")
          == match.canon("LAC", "NBA") == "NBA:LAC")
    check("league code: 'NBA' yes, 'NBA Summer League' no",
          match.pin_league_code("NBA") == "NBA" and match.pin_league_code("NBA Summer League") is None)

    # ---------------------------------------------------------------- game matching
    t0 = 1_800_000_000.0
    nba = [{"pid": "1", "sport": "basketball", "league": "NBA", "home": "Charlotte Hornets",
            "away": "Cleveland Cavaliers", "start_ts": t0 + 5 * 3600, "parent_id": None}]
    g = {"teams": ["Cleveland", "Charlotte"], "sport": "basketball", "league_code": "NBA", "women": False,
         "ref_ts": t0 + 8 * 3600}
    ev, sides, sc, note = match.match_game(g, nba)
    check("match: Kalshi CLE vs CHA -> Pinnacle event, sides by name",
          ev and sides == {"Cleveland": "away", "Charlotte": "home"})
    ev, *_ = match.match_game(dict(g, ref_ts=t0 + 30 * 3600), nba)
    check("match: start more than 18h away -> no match", ev is None)
    ev, *_ = match.match_game(dict(g, women=True), nba)
    check("match: women's game never pairs with a men's event", ev is None)
    two = nba + [dict(nba[0], pid="2", start_ts=t0 + 6 * 3600)]
    ev, _, _, note = match.match_game(g, two)
    check("match: two candidate events -> skipped as ambiguous", ev is None and "ambiguous" in note)
    fair = {"home": 0.6, "away": 0.4}
    dup = [dict(nba[0], fair=fair), dict(nba[0], pid="1b", fair=dict(fair))]
    ev, sides, sc, note = match.match_game(g, dup)
    check("match: one game listed twice by Pinnacle with the same prices -> treated as one (lowest id)",
          ev and ev["pid"] == "1" and "identical" in note)
    dup[1] = dict(nba[0], pid="1b", fair={"home": 0.7, "away": 0.3})
    ev, _, _, note = match.match_game(g, dup)
    check("match: same teams and start but different prices (e.g. a corners line) -> skipped", ev is None
          and "ambiguous" in note)
    soc = [{"pid": "9", "sport": "soccer", "league": "England - Premier League", "home": "Manchester City",
            "away": "Fulham", "start_ts": t0, "parent_id": None}]
    ev, sides, *_ = match.match_game({"teams": ["Manchester City", "Fulham"], "sport": "soccer", "league_code": None,
                                      "women": False, "ref_ts": t0}, soc)
    check("match: soccer by fuzzy names", ev and sides == {"Manchester City": "home", "Fulham": "away"})

    # ---------------------------------------------------------------- engine: scheduler, quotes, bets, closes
    clk = Clock(t0)
    st = Store(os.path.join(tmp, "e.db"))
    pin, kal, pmu = FakePin(clk), FakeVenue(clk, "kalshi"), FakeVenue(clk, "polyus")
    start = t0 + 5 * 3600
    pin.payload["basketball"] = [pev(1, "NBA", "Charlotte Hornets", "Cleveland Cavaliers", start, 2.1, 1.8)]
    kal.listing = [{"venue": "kalshi", "gid": "KXNBAGAME-X", "sport": "basketball", "series": "KXNBAGAME",
                    "league": "Pro Basketball", "league_code": "NBA", "title": "Cleveland vs Charlotte",
                    "ref_ts": start + 3 * 3600, "women": False, "teams": ["Cleveland", "Charlotte"],
                    "outcomes": [{"key": "K-CLE", "name": "Cleveland", "kind": "team"},
                                 {"key": "K-CHA", "name": "Charlotte", "kind": "team"}]}]
    pmu.listing = [{"venue": "polyus", "gid": "cle-cha", "sport": "basketball", "league": "NBA",
                    "league_code": "NBA", "title": "Cavaliers vs Hornets", "ref_ts": start, "women": False,
                    "teams": ["Cavaliers", "Hornets"],
                    "outcomes": [{"key": "p-cle", "name": "Cavaliers", "kind": "team", "fee": 0.095},
                                 {"key": "p-cha", "name": "Hornets", "kind": "team", "fee": 0.095}]}]
    fair_cle = (1 / 1.8) / (1 / 1.8 + 1 / 2.1)
    kal.quotes = {"K-CLE": {"ask": 0.50, "depth": 500, "bid": 0.49}, "K-CHA": {"ask": 0.52, "depth": 500, "bid": 0.50}}
    pmu.quotes = {"p-cle": {"ask": 0.53, "depth": 500, "bid": 0.52}, "p-cha": {"ask": 0.40, "depth": 5, "bid": 0.39}}
    eng = Engine(st, kal=kal, pmu=pmu, pin=pin, clock=clk)
    eng.refresh_listings()
    due = eng.due()
    check("scheduler: nothing matched yet -> one first-look snapshot of the sport", due == [(2, "basketball", "bootstrap")])
    eng.tick()
    gs = {g["gid"]: g for g in eng.games()}
    check("both venues' games matched to the Pinnacle event", gs["KXNBAGAME-X"]["pid"] == "1" and gs["cle-cha"]["pid"] == "1")
    bets = st.rows("bets")
    exp_edge = fair_cle - 0.50 - 0.07 * 0.25
    check("entry: Kalshi Cleveland at 50c vs fair %.3f -> paper bet (edge %.1fc)" % (fair_cle, exp_edge * 100),
          len(bets) == 1 and bets[0]["id"] == "kalshi:K-CLE" and close(bets[0]["edge_entry"], exp_edge))
    check("entry: size 100, fee rounded up per bet", bets[0]["n"] == 100 and close(bets[0]["fee_total"], 1.75))
    qs = {r["okey"]: r for r in st.rows("quotes")}
    check("no bet: Polymarket US Cavaliers at 53c (edge < 1c after its 0.095 fee)", qs["p-cle"]["edge"] < 0.01
          and close(qs["p-cle"]["fee"], 0.095 * 0.53 * 0.47))
    check("no bet: under 10 contracts at the ask", qs["p-cha"]["why"] == "under 10 contracts at the ask")
    check("no close yet: snapshot was 5h before the start", bets[0]["close_ts"] is None)
    check("scheduler: right after a snapshot nothing is due", eng.due() == [])
    clk.t = t0 + 3 * 3600 + 1
    check("scheduler: 3h later with a matched game inside 24h -> baseline", eng.due() == [(1, "basketball", "baseline")])
    kal.stale = 200
    eng.tick()
    qs = {r["okey"]: r for r in st.rows("quotes", "WHERE snap_id=2")}
    check("venue price older than 120s is not used", qs["K-CHA"]["why"] == "venue price too old")
    kal.stale = 0
    clk.t = start - 34 * 60
    check("scheduler: game starts within 35 min -> pre-start snapshot", eng.due()[0] == (0, "basketball", "prestart"))
    pin.payload["basketball"] = [pev(1, "NBA", "Charlotte Hornets", "Cleveland Cavaliers", start, 2.2, 1.75)]
    eng.tick()
    b = st.rows("bets")[0]
    fair_close = (1 / 1.75) / (1 / 1.75 + 1 / 2.2)
    check("close: pre-start snapshot sets Pinnacle's closing fair", close(b["fair_close"], fair_close)
          and b["close_snap"] == 3)
    check("primary: close 4.4h after entry and 34 min before start -> in the primary sample", report.is_primary(b))
    check("CLV $ = n x (close - ask) - fee", close(report.clv(b), 100 * (fair_close - 0.5) - 1.75))
    clk.t = start - 60
    eng.tick()
    b = st.rows("bets")[0]
    check("start price captured just before the start", b["start_bid"] == 0.49 and b["start_ask"] == 0.50)
    clk.t = start + 3 * 3600 + 60
    kal.results["K-CLE"] = ("yes", 1.0)
    eng.settle_ts = 0
    eng.tick()
    b = st.rows("bets")[0]
    check("settlement: Kalshi result yes -> P&L = n x (1 - ask) - fee", b["result"] == "yes"
          and close(report.pnl(b), 100 * 0.5 - 1.75))

    s = eng.summary()
    check("summary line builds (bets, matching, report)", s["bets"]["total"] == 1 and s["matching"].get("kalshi:matched") == 1
          and s["matching"].get("polyus:matched") == 1 and json.dumps(s, default=str))

    # special rows (parent_id) are never stored, so a restart can't turn them into extra games
    pin.payload["basketball"] = [pev(1, "NBA", "Charlotte Hornets", "Cleveland Cavaliers", start + 86400, 2.2, 1.75),
                                 dict(pev(77, "NBA", "Charlotte Hornets", "Cleveland Cavaliers", start + 86400, 1.5, 2.6),
                                      parent_id=1)]
    clk.t = start + 4 * 3600
    eng.snapshot("basketball", "baseline")
    e3 = Engine(st, kal=kal, pmu=pmu, pin=pin, clock=clk)
    check("restart: Pinnacle special rows don't come back as games", set(e3.pin_latest["basketball"]) == {"1"})
    # a bet entered at the last snapshot before the start has no later close: settlement only
    b2 = dict(b, id="x", entry_ts=b["close_ts"])
    check("primary: entry at the closing snapshot is not in the primary sample", not report.is_primary(b2))

    # ---------------------------------------------------------------- budget
    st2 = Store(os.path.join(tmp, "b.db"))
    now = t0
    st2.many("INSERT INTO pin_calls VALUES (?,?,?,?)", [(now - 100 - i, "soccer", "ok", "x") for i in range(70)])
    clk2 = Clock(now)
    e2 = Engine(st2, kal=kal, pmu=pmu, pin=FakePin(clk2), clock=clk2)
    check("budget: baseline held back at 70 of 90", e2.snapshot("basketball", "baseline") is None
          and pinnacle.calls_last_24h(st2, now) == 70)
    check("budget: pre-start still allowed at 70", e2.snapshot("basketball", "prestart") is not None)
    st2.many("INSERT INTO pin_calls VALUES (?,?,?,?)", [(now - 50 - i, "soccer", "ok", "x") for i in range(19)])
    check("budget: hard cap 90 per rolling 24h", e2.snapshot("hockey", "prestart") is None
          and pinnacle.calls_last_24h(st2, now) == 90)
    clk2.t = now + 86400
    check("budget: calls older than 24h free up (refused calls never count)", pinnacle.calls_last_24h(st2, clk2.t) == 0)

    # ---------------------------------------------------------------- report and verdict
    def fake_bet(i, pid, ask, close_f, val, entry, venue="kalshi"):
        return {"id": f"b{i}", "venue": venue, "pid": pid, "sport": "basketball", "ask": ask, "n": 100.0,
                "fee_total": ceil_cent(fee_per_contract(venue, ask) * 100), "fair_entry": close_f,
                "edge_entry": 0.015, "fair_close": close_f, "close_ts": entry + 3600, "entry_ts": entry,
                "start_ts": entry + 3600 + 1800, "result": "yes" if val == 1 else "no", "settle_value": val,
                "excluded": None, "start_bid": None, "start_ask": None}
    same = [fake_bet(i, str(i), 0.5, 0.55, 1 if i % 2 else 0, t0) for i in range(10)]
    ci = report.boot(same, report.clv, n=500)
    check("bootstrap: identical games -> CI collapses to the mean", close(ci[0], ci[1], 1e-9)
          and close(ci[0], report.roi(same, report.clv)))
    base = {"primary_bets": 80, "clv_ci95": [0.01, 0.05], "clv_week1": 0.02, "clv_week2": 0.03,
            "pnl_per_dollar": 0.01, "pnl_ci95": [-0.2, 0.2]}
    check("verdict: PASS", report.verdict(base) == "PASS")
    check("verdict: CLV CI below 0 -> REJECT", report.verdict(dict(base, clv_ci95=[-0.05, -0.01])) == "REJECT")
    check("verdict: settlement P&L CI below 0 -> REJECT", report.verdict(dict(base, pnl_ci95=[-0.3, -0.01])) == "REJECT")
    check("verdict: under 30 primary bets -> TOO FEW", report.verdict(dict(base, primary_bets=20)) == "TOO FEW")
    check("verdict: 45 bets -> INCONCLUSIVE (needs 60)", report.verdict(dict(base, primary_bets=45)) == "INCONCLUSIVE")
    check("verdict: negative week 2 -> INCONCLUSIVE", report.verdict(dict(base, clv_week2=-0.01)) == "INCONCLUSIVE")
    check("verdict: negative settlement P&L -> INCONCLUSIVE", report.verdict(dict(base, pnl_per_dollar=-0.01))
          == "INCONCLUSIVE")
    st3 = Store(os.path.join(tmp, "r.db"))
    ws = time.strftime("%Y-%m-%d", time.gmtime(t0))
    st3.set_meta("window_start", ws)
    w = report.window(st3)
    rows = [fake_bet(i, str(i // 2), 0.5, 0.56, 1 if i % 3 else 0, w[0] + 3600 + i * 3 * 3600) for i in range(80)]
    cols = st3.cols("bets")
    st3.many(f"INSERT INTO bets ({','.join(rows[0].keys())}) VALUES ({','.join('?' * len(rows[0]))})",
             [tuple(r.values()) for r in rows])
    r = report.build(st3, now=w[0] + 3 * 86400)
    check("report: in progress before day 14", r["kalshi"]["verdict"].startswith("IN PROGRESS (3/14"))
    r = report.build(st3, now=w[1] + 13 * 3600)
    check("report: verdict after the window (+12h for settlement): 80 bets at +8.5c CLV -> PASS",
          r["kalshi"]["verdict"] == "PASS" and r["kalshi"]["primary_bets"] == 80 and "fair_close" in cols)
    check("report: Polymarket US with no bets -> TOO FEW", r["polyus"]["verdict"] == "TOO FEW")
    print("ALL PASS" if ok_all else "SOME FAILED", flush=True)
    return ok_all


if __name__ == "__main__":
    main()
