"""Kalshi public API (no key): game-winner series, open games, order books, results."""
import re
import time
from datetime import datetime

from . import net

BASE = "https://external-api.kalshi.com/trade-api/v2"
TAG_SPORT = {"basketball": "basketball", "hockey": "hockey", "football": "football", "soccer": "soccer"}
SKIP_SERIES = {"KXNBAALLSTARGAME", "KXNBACELEBRITYGAME", "KXJUDGEMENTDAYGAME", "KXBIG3GAME", "KXAFLGAME",
               "KX1HOMEGAME", "KX1STHOMEGAME"}
LEAGUE_CODE = {"KXNBAGAME": "NBA", "KXNHLGAME": "NHL", "KXNFLGAME": "NFL"}
WOMEN_SERIES = re.compile(r"(NCAAWB|WNBA|NWSL|BBW|WGAME$)")


def _get(path, params=None):
    return net.get_json(BASE, path, params, retries=3)


def game_series():
    """{series_ticker: sport} for game-winner series in the four PREREG sports."""
    d = _get("/series", {"category": "Sports"}) or {}
    out = {}
    for s in d.get("series") or []:
        t = s.get("ticker") or ""
        if not t.endswith("GAME") or t in SKIP_SERIES:
            continue
        tags = [str(x).lower() for x in (s.get("tags") or [])]
        if "aussie rules" in tags:
            continue
        sport = next((TAG_SPORT[x] for x in tags if x in TAG_SPORT), None)
        if sport:
            out[t] = sport
    return out


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


_empty = {}   # series with no open events -> when we last looked (re-check every 6h; keeps the load light)


def open_games(series):
    """Open game events -> venue game dicts (one per event). Gentle on the API: ~4 calls a second, and series
    with nothing open are re-checked only every 6 hours (the LIP bot shares Kalshi's public rate limits)."""
    out = []
    now = time.time()
    for st, sport in series.items():
        if now - _empty.get(st, 0) < 6 * 3600:
            continue
        cursor, found = None, 0
        for _ in range(20):
            p = {"series_ticker": st, "status": "open", "with_nested_markets": "true", "limit": 200}
            if cursor:
                p["cursor"] = cursor
            d = _get("/events", p) or {}
            evs = d.get("events") or []
            found += len(evs)
            for e in evs:
                g = _game(e, st, sport)
                if g:
                    out.append(g)
            cursor = d.get("cursor") or d.get("next_cursor")
            time.sleep(0.25)
            if not cursor or not evs:
                break
        if found:
            _empty.pop(st, None)
        else:
            _empty[st] = now
    return out


def _game(e, st, sport):
    pm = e.get("product_metadata") or {}
    if pm.get("competition_scope") and str(pm.get("competition_scope")).lower() != "game":
        return None
    mk = [m for m in e.get("markets") or [] if m.get("status") in ("active", "open", "initialized")]
    if not mk:
        return None
    outs, teams = [], []
    for m in mk:
        name = str(m.get("yes_sub_title") or "").strip()
        kind = "draw" if name.lower() in ("tie", "draw") else "team"
        outs.append({"key": m["ticker"], "name": name, "kind": kind})
        if kind == "team":
            teams.append(name)
    want = 3 if sport == "soccer" else 2
    if len(outs) != want or len(teams) != 2 or len(set(teams)) != 2:
        return None
    ref = [_ts(m.get("occurrence_datetime") or m.get("expected_expiration_time")) for m in mk]
    ref = [r for r in ref if r]
    if not ref:
        return None
    comp = str(pm.get("competition") or "")
    return {"venue": "kalshi", "gid": e["event_ticker"], "sport": sport, "series": st, "league": comp or st,
            "league_code": LEAGUE_CODE.get(st), "title": e.get("title") or "", "ref_ts": min(ref),
            "women": bool(WOMEN_SERIES.search(st)) or "women" in comp.lower(), "teams": teams, "outcomes": outs}


def books(tickers):
    """{ticker: {'ask', 'depth', 'bid', 'ts'}} for the YES side: ask = 1 - best NO bid (depth = that bid's size)."""
    out = {}
    tickers = list(dict.fromkeys(t for t in tickers if t))
    for i in range(0, len(tickers), 100):
        d = _get("/markets/orderbooks", {"tickers": tickers[i:i + 100]}) or {}
        ts = time.time()
        for ob in d.get("orderbooks") or []:
            fp = ob.get("orderbook_fp") or {}
            yes = _levels(fp.get("yes_dollars"))
            no = _levels(fp.get("no_dollars"))
            q = {"ts": ts, "ask": None, "depth": 0.0, "bid": None}
            if no:
                q["ask"] = round(1.0 - no[0][0], 4)
                q["depth"] = no[0][1]
            if yes:
                q["bid"] = yes[0][0]
            out[ob.get("ticker")] = q
    return out


def _levels(lv):
    agg = {}
    for x in lv or []:
        try:
            p, s = float(x[0]), float(x[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0 and 0 < p < 1:
            agg[p] = agg.get(p, 0.0) + s
    return sorted(agg.items(), key=lambda z: -z[0])


def result(ticker):
    """('yes'|'no'|'void'|None, settle value or None). None while unsettled."""
    m = (_get(f"/markets/{ticker}") or {}).get("market") or {}
    st = str(m.get("status") or "").lower()
    res = str(m.get("result") or "").lower()
    if st in ("settled", "finalized", "determined") or res in ("yes", "no"):
        if res == "yes":
            return "yes", 1.0
        if res == "no":
            return "no", 0.0
        if res in ("void", "voided", "scalar") or st == "voided":
            return "void", None
    return None, None
