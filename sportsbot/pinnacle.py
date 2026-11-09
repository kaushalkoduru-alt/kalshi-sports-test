"""Pinnacle odds via pinnapi.com (free tier: 100 requests a day). One request = all prematch fixtures of a sport.
Every attempt is logged against the budget (PREREG: 90 per rolling 24h), and there are no automatic retries."""
import time

from . import net
from .config import PRE, PINNAPI_KEY, SPORTS, THREE_WAY

BASE = "https://pinnapi.com"


class BudgetExhausted(Exception):
    pass


def calls_last_24h(store, now=None):
    now = now or time.time()
    return store.q("SELECT COUNT(*) FROM pin_calls WHERE ts > ?", (now - 86400,))[0][0]


def fetch(store, sport, reason):
    """One budgeted request. Returns the raw payload or raises (BudgetExhausted / net.HTTPFail)."""
    if not PINNAPI_KEY:
        raise net.HTTPFail("PINNAPI_KEY not set")
    now = time.time()
    if calls_last_24h(store, now) >= PRE["pin_budget_24h"]:
        raise BudgetExhausted(f"{PRE['pin_budget_24h']} requests in the last 24h")
    status = "ok"
    try:
        return net.get_json(BASE, "/kit/v1/prematch/fixtures", {"sport_id": SPORTS[sport]},
                            headers={"x-portal-apikey": PINNAPI_KEY}, retries=0, timeout=40)
    except net.HTTPFail as e:
        status = f"{e.status or 'net'}"
        raise
    finally:
        store.x("INSERT INTO pin_calls VALUES (?,?,?,?)", (now, sport, status, reason))


def _ts(s):
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s) / (1000.0 if s > 1e11 else 1.0)
    try:
        from datetime import datetime
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _num(x):
    try:
        v = float(x)
        return v if v > 1.0 else None
    except (TypeError, ValueError):
        return None


def parse(payload, sport):
    """-> list of events {pid, sport, league, home, away, start_ts, parent_id, odds{home,away,draw}, fair{...},
    fair_prop{...}, fair_pow{...}, skip}. fair = the lower of the two de-vigs (amendment v1.2)."""
    evs = (payload or {}).get("events") if isinstance(payload, dict) else payload
    if isinstance(evs, dict):
        evs = list(evs.values())
    out = []
    for e in evs or []:
        if not isinstance(e, dict):
            continue
        per = e.get("periods") or {}
        p0 = per.get("num_0") if isinstance(per, dict) else None
        if p0 is None and isinstance(per, list):
            p0 = next((p for p in per if isinstance(p, dict) and p.get("number") == 0), None)
        ml = (p0 or {}).get("money_line") or {}
        odds = {"home": _num(ml.get("home")), "away": _num(ml.get("away")), "draw": _num(ml.get("draw"))}
        ev = {"pid": str(e.get("event_id")), "sport": sport, "league": str(e.get("league_name") or ""),
              "league_id": e.get("league_id"), "home": str(e.get("home") or ""), "away": str(e.get("away") or ""),
              "start_ts": _ts(e.get("starts") or e.get("start_ts")), "parent_id": e.get("parent_id"),
              "status": e.get("status"), "odds": odds, "fair": None, "fair_prop": None, "fair_pow": None,
              "skip": None}
        if not ev["start_ts"] or not ev["home"] or not ev["away"]:
            ev["skip"] = "missing start or teams"
        else:
            fairs(ev, sport)
        out.append(ev)
    return out


def fairs(ev, sport):
    """Fill fair_prop (v1 proportional), fair_pow (power) and fair = the lower of the two per outcome (amendment v1.2:
    the rules use fair). Returns ev."""
    ev["fair_prop"], ev["skip"] = devig(ev["odds"], sport)
    ev["fair_pow"] = devig_power(ev["odds"], sport) if ev["fair_prop"] else None
    ev["fair"] = cons_fair(ev["fair_prop"], ev["fair_pow"])
    if ev["fair_prop"] and not ev["fair"]:
        ev["skip"] = "power de-vig failed"
    return ev


def devig_power(odds, sport):
    """Power de-vig (amendment v1.2): fair_i = r_i^k with r_i = 1/odds_i and k solved so the fair prices sum to 1.
    It takes more of the margin off longshots than proportional de-vig does."""
    keys = ("home", "draw", "away") if sport in THREE_WAY else ("home", "away")
    if any(not odds.get(k) for k in keys) or (sport not in THREE_WAY and odds.get("draw")):
        return None
    r = [1.0 / odds[k] for k in keys]
    if any(not 0 < v < 1 for v in r):
        return None

    def excess(k):
        return sum(v ** k for v in r) - 1.0      # falls as k rises (every r < 1)
    lo, hi = 0.2, 5.0
    if excess(lo) < 0 or excess(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if excess(mid) > 0:
            lo = mid
        else:
            hi = mid
    k = (lo + hi) / 2.0
    f = [v ** k for v in r]
    s = sum(f)
    return {key: x / s for key, x in zip(keys, f)}


def cons_fair(fair_prop, fair_pow):
    """Amendment v1.2: an outcome's fair price is the lower of the two de-vigs, so an edge counts only if both see it."""
    if not fair_prop or not fair_pow:
        return None
    return {k: min(fair_prop[k], fair_pow[k]) for k in fair_prop}


def devig(odds, sport):
    """Proportional de-vig (PREREG Fair value). Soccer must be 3-way; other sports 2-way with no draw price."""
    if sport in THREE_WAY:
        keys = ("home", "draw", "away")
    else:
        if odds.get("draw"):
            return None, "draw price on a 2-way sport"
        keys = ("home", "away")
    if any(not odds.get(k) for k in keys):
        return None, "missing price"
    imp = {k: 1.0 / odds[k] for k in keys}
    s = sum(imp.values())
    if s <= 0:
        return None, "bad prices"
    return {k: v / s for k, v in imp.items()}, None
