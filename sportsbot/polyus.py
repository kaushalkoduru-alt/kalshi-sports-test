"""Polymarket US public API (no key): game-winner events, order books, settlement.

Shapes (checked against live payloads on Nov 9, 2026, PREREG amendment v1.1):
- 2-way games (basketball, hockey, football): ONE market, sportsMarketTypeV2 = SPORTS_MARKET_TYPE_MONEYLINE, whose
  long side is one team and short side the other. Buying the short-side team costs 1 - the long side's best bid.
- soccer: THREE markets (home, away, tie), SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME, regulation time; each outcome is
  bought on its market's long side.
- every market carries its own taker feeCoefficient (Polymarket US fee = coef x C x p x (1 - p))."""
import re
import threading
import time
from datetime import datetime, timezone

from . import match, net
from .config import OPS

BASE = "https://gateway.polymarket.us"
ML = "SPORTS_MARKET_TYPE_MONEYLINE"
DRAW = "SPORTS_MARKET_TYPE_DRAWABLE_OUTCOME"
SPORT_TAGS = {"basketball": "basketball", "hockey": "hockey", "football": "football", "soccer": "soccer",
              "nba": "basketball", "wnba": "basketball", "cbb": "basketball", "wcbb": "basketball",
              "ncaab": "basketball", "euroleague": "basketball", "nhl": "hockey", "ahl": "hockey", "khl": "hockey",
              "nfl": "football", "cfb": "football", "ncaaf": "football", "ufl": "football", "cfl": "football"}
WOMEN_TAGS = {"wnba", "wcbb", "ncaaw", "uwcl", "ligaf", "wsl", "fawsl", "nwsl", "cbb-w", "women", "womens",
              "frauen-bundesliga", "d1f", "liga-f", "awsl"}
LEAGUE_CODE = {"nba": "NBA", "nhl": "NHL", "nfl": "NFL"}
_rl = threading.Lock()
_last = [0.0]


def _get(path, params=None):
    with _rl:
        wait = _last[0] + 1.0 / OPS["polyus_rps"] - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    return net.get_json(BASE, path, params, retries=2)


def raw(path, params=None):
    return _get(path, params)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def list_events(t0, t1, max_pages=60):
    out, off = [], 0
    for _ in range(max_pages):
        d = _get("/v1/events", {"active": "true", "closed": "false", "startTimeMin": _iso(t0),
                                "startTimeMax": _iso(t1), "limit": 100, "offset": off}) or {}
        evs = d.get("events") or []
        out += evs
        if len(evs) < 100:
            break
        off += 100
    return out


def _team_name(side, event_teams):
    """Longest descriptive name for a market side's team ('New York Rangers' beats 'Rangers', 'Buffalo Bills'
    beats 'BUF Bills')."""
    t = side.get("team") or {}
    names = [t.get("name"), t.get("safeName")]
    if not any(names):
        d = (side.get("description") or "").lower()
        for et in event_teams:
            if d and d in {str(et.get(k) or "").lower() for k in ("alias", "safeName", "name", "abbreviation")}:
                names = [et.get("name"), et.get("safeName")]
                break
    names = [n for n in names if n and not re.match(r"^[A-Z]{2,4} ", n)] or [n for n in names if n]
    return max(names, key=len) if names else None


def _fee(m):
    try:
        f = float(m.get("feeCoefficient"))
        return f if 0 < f < 1 else None
    except (TypeError, ValueError):
        return None


def parse_event(e):
    """-> (game dict or None, reason)."""
    tags = [str(t.get("slug") or "").lower() for t in e.get("tags") or []]
    if any(t in ("esports", "esoccer", "ebattles", "ebfpl") or t.startswith("e-") for t in tags) or \
            str(e.get("title") or "").lower().startswith(("ebattles", "esoccer", "e-soccer")):
        return None, "not one of the four sports"
    sport = next((SPORT_TAGS[t] for t in tags if t in SPORT_TAGS), None)
    if not sport:
        return None, "not one of the four sports"
    start = _ts(e.get("startTime"))
    if not start:
        return None, "no start time"
    mk = [m for m in e.get("markets") or [] if m.get("active", True) and not m.get("closed")]
    teams_ev = e.get("teams") or []
    outs = []
    if sport == "soccer":
        dm = [m for m in mk if m.get("sportsMarketTypeV2") == DRAW]
        if len(dm) != 3:
            return None, f"soccer with {len(dm)} outcome markets"
        for m in dm:
            long_ = [s for s in m.get("marketSides") or [] if s.get("long")]
            title = str(m.get("title") or "")
            if re.match(r"^(tie|draw)\b", title, re.I):
                outs.append({"key": m["slug"], "name": "Tie", "kind": "draw", "fee": _fee(m)})
            else:
                nm = _team_name(long_[0], teams_ev) if long_ else None
                if not nm:
                    return None, "soccer market without a team"
                outs.append({"key": m["slug"], "name": nm, "kind": "team", "fee": _fee(m)})
    else:
        ml = [m for m in mk if m.get("sportsMarketTypeV2") == ML]
        if len(ml) != 1:
            return None, f"{len(ml)} moneyline markets"
        m = ml[0]
        sides = m.get("marketSides") or []
        lo = [s for s in sides if s.get("long") is True]
        sh = [s for s in sides if s.get("long") is False]
        if len(lo) != 1 or len(sh) != 1:
            return None, "moneyline without one long and one short side"
        a, b = _team_name(lo[0], teams_ev), _team_name(sh[0], teams_ev)
        if not a or not b or a == b:
            return None, "moneyline team names missing"
        outs = [{"key": m["slug"] + "#long", "name": a, "kind": "team", "fee": _fee(m)},
                {"key": m["slug"] + "#short", "name": b, "kind": "team", "fee": _fee(m)}]
    teams = [o["name"] for o in outs if o["kind"] == "team"]
    if len(teams) != 2 or len(set(teams)) != 2:
        return None, "not two distinct teams"
    words = " ".join(tags)
    lc = next((v for k, v in LEAGUE_CODE.items() if k in tags), None)
    return {"venue": "polyus", "gid": str(e.get("slug") or e.get("id")), "sport": sport,
            "league": next((t for t in tags if t not in ("sports", "games", sport)), ""), "league_code": lc,
            "title": e.get("title") or "", "ref_ts": start,
            "women": bool(WOMEN_TAGS & set(tags)) or match.is_women(e.get("title"), *teams),
            "teams": teams, "outcomes": outs}, "ok"


def games(t0, t1, raw_out=None):
    out, odd = [], []
    evs = list_events(t0, t1)
    if raw_out is not None:
        raw_out.extend(evs)
    for e in evs:
        g, why = parse_event(e)
        if g:
            out.append(g)
        elif why != "not one of the four sports":
            odd.append(f"{e.get('slug')}: {why}")
    return out, odd


def _amt(a):
    if isinstance(a, dict):
        a = a.get("value")
    try:
        return float(a)
    except (TypeError, ValueError):
        return None


def book(slug):
    """Best bid/offer of the market's long side with the size at each: (bid, bid_qty, ask, ask_qty, ts)."""
    d = _get(f"/v1/markets/{slug}/book") or {}
    m = d.get("marketData") or d
    bids = [(_amt(x.get("px")), _amt(x.get("qty"))) for x in m.get("bids") or []]
    offs = [(_amt(x.get("px")), _amt(x.get("qty"))) for x in m.get("offers") or []]
    bids = [x for x in bids if x[0] is not None and x[1]]
    offs = [x for x in offs if x[0] is not None and x[1]]
    bb = max(bids, key=lambda x: x[0]) if bids else (None, 0.0)
    bo = min(offs, key=lambda x: x[0]) if offs else (None, 0.0)
    if str(m.get("state") or "MARKET_STATE_OPEN") != "MARKET_STATE_OPEN":
        return None, 0.0, None, 0.0, time.time()
    return bb[0], bb[1], bo[0], bo[1], time.time()


def books(keys):
    """{key: {'ask', 'depth', 'bid', 'ts'}} for 'this outcome wins'. key = slug (soccer, long side) or
    slug#long / slug#short (2-way games)."""
    out, cache = {}, {}
    for k in dict.fromkeys(x for x in keys if x):
        slug, _, side = k.partition("#")
        if slug not in cache:
            try:
                cache[slug] = book(slug)
            except net.HTTPFail:
                cache[slug] = None
        b = cache[slug]
        if not b:
            continue
        bid, bq, ask, aq, ts = b
        if side == "short":
            out[k] = {"ts": ts, "ask": None if bid is None else round(1.0 - bid, 4), "depth": bq or 0.0,
                      "bid": None if ask is None else round(1.0 - ask, 4)}
        else:
            out[k] = {"ts": ts, "ask": ask, "depth": aq or 0.0, "bid": bid}
    return out


def result(key):
    slug, _, side = key.partition("#")
    try:
        d = _get(f"/v1/markets/{slug}/settlement") or {}
    except net.HTTPFail as e:
        if e.status in (400, 404):
            return None, None
        raise
    try:
        v = float(d.get("settlement"))
    except (TypeError, ValueError):
        return None, None
    if side == "short":
        v = 1.0 - v
    if v >= 0.999:
        return "yes", 1.0
    if v <= 0.001:
        return "no", 0.0
    return "void", None
