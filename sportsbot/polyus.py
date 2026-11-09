"""Polymarket US public API (no key): sports game events, best bid/ask, settlement.
Shapes follow Polymarket's official SDK (polymarket-us-python); the parser is checked against live payloads in
probe mode before the window starts."""
import re
import threading
import time
from datetime import datetime, timezone

from . import net
from .config import OPS

BASE = "https://gateway.polymarket.us"
_rl = threading.Lock()
_last = [0.0]

SPORT_WORDS = {
    "basketball": ("nba", "ncaab", "cbb", "wnba", "euroleague", "basketball", "ncaamb", "ncaawb"),
    "hockey": ("nhl", "hockey", "ahl", "khl"),
    "football": ("nfl", "ncaaf", "cfb", "american-football", "college-football"),
    "soccer": ("soccer", "epl", "premier-league", "la-liga", "laliga", "serie-a", "bundesliga", "ligue-1", "ucl",
               "champions-league", "mls", "uefa", "europa", "eredivisie", "liga-mx", "fa-cup", "efl", "fifa"),
}
LEAGUE_CODE = {"nba": "NBA", "nhl": "NHL", "nfl": "NFL"}
VS = re.compile(r"\s+(?:vs\.?|v\.?|@|at)\s+", re.I)


def _get(path, params=None):
    with _rl:
        wait = _last[0] + 1.0 / OPS["polyus_rps"] - time.time()
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    return net.get_json(BASE, path, params, retries=2)


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def list_events(t0, t1, max_pages=40):
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


def _sport_of(e):
    words = " ".join([str(t.get("slug") or "") + " " + str(t.get("label") or "") for t in e.get("tags") or []]
                     + [str((e.get("series") or {}).get("slug") or ""), str(e.get("slug") or "")]).lower()
    for sport, keys in SPORT_WORDS.items():
        if any(k in words for k in keys):
            return sport, words
    return None, words


def games(t0, t1):
    """Venue game dicts (same shape as kalshi.open_games) from Polymarket US events that look like game winners:
    2 team outcomes (+ a draw for soccer), one market per outcome."""
    out, odd = [], []
    for e in list_events(t0, t1):
        sport, words = _sport_of(e)
        mk = [m for m in e.get("markets") or [] if m.get("active", True) and not m.get("closed")]
        outs, teams = [], []
        for m in mk:
            name = str(m.get("outcome") or m.get("title") or "").strip()
            kind = "draw" if name.lower() in ("draw", "tie") else "team"
            outs.append({"key": m.get("slug"), "name": name, "kind": kind})
            if kind == "team":
                teams.append(name)
        want = 3 if sport == "soccer" else 2
        start = _ts(e.get("startTime"))
        if not start or len(teams) != 2 or len(set(teams)) != 2 or len(outs) not in (2, 3) \
                or (sport and len(outs) != want) or any(not o["key"] for o in outs):
            odd.append(e.get("slug"))
            continue
        lc = next((v for k, v in LEAGUE_CODE.items() if re.search(rf"\b{k}\b", words)), None)
        out.append({"venue": "polyus", "gid": str(e.get("slug") or e.get("id")), "sport": sport,
                    "league": (e.get("series") or {}).get("title") or "", "league_code": lc,
                    "title": e.get("title") or "", "ref_ts": start,
                    "women": bool(re.search(r"\b(wnba|women|nwsl|ncaaw)", words)),
                    "teams": teams, "outcomes": outs})
    return out, odd


def _amt(a):
    if isinstance(a, dict):
        a = a.get("value")
    try:
        return float(a)
    except (TypeError, ValueError):
        return None


def bbo(slug):
    """{'ask', 'depth', 'bid', 'ts'} for 'this outcome wins' (the market's long side)."""
    d = _get(f"/v1/markets/{slug}/bbo") or {}
    m = d.get("marketData") or d
    return {"ts": time.time(), "ask": _amt(m.get("bestAsk")), "bid": _amt(m.get("bestBid")),
            "depth": float(m.get("askDepth") or 0)}


def books(slugs):
    out = {}
    for s in dict.fromkeys(x for x in slugs if x):
        try:
            out[s] = bbo(s)
        except net.HTTPFail:
            continue
    return out


def result(slug):
    try:
        d = _get(f"/v1/markets/{slug}/settlement") or {}
    except net.HTTPFail as e:
        if e.status in (400, 404):
            return None, None
        raise
    v = d.get("settlement")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None, None
    if v >= 0.999:
        return "yes", 1.0
    if v <= 0.001:
        return "no", 0.0
    return "void", None
