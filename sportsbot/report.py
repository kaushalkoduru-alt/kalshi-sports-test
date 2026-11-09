"""PREREG metrics and the per-venue verdict, computed from the database on demand."""
import random
import time
from datetime import datetime, timezone

from . import config as C

PRE = C.PRE
VENUES = ("kalshi", "polyus")


def window(store):
    ws = C.WINDOW_START or store.meta("window_start")
    if not ws:
        return None
    start = datetime.strptime(ws, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    return start, start + PRE["window_days"] * 86400


def is_primary(b):
    return (b["excluded"] is None and b["close_ts"] is not None and b["fair_close"] is not None
            and b["close_ts"] - b["entry_ts"] >= PRE["close_min_gap"] - 1e-6
            and b["start_ts"] - b["close_ts"] <= PRE["close_max_before"] + 1e-6)


def clv(b):
    return b["n"] * (b["fair_close"] - b["ask"]) - b["fee_total"]


def pnl(b):
    return b["n"] * (b["settle_value"] - b["ask"]) - b["fee_total"]


def clv_prop(b):
    """Secondary (amendment v1.2): CLV with the v1 proportional fair at the close."""
    return b["n"] * (b["fair_close_prop"] - b["ask"]) - b["fee_total"]


def exp_at_entry(b):
    return b["n"] * (b["fair_entry"] - b["ask"]) - b["fee_total"]


def risked(b):
    return b["n"] * b["ask"]


def roi(bets, f):
    den = sum(risked(b) for b in bets)
    return (sum(f(b) for b in bets) / den) if den > 0 else None


def boot(bets, f, n=None, seed=11):
    """95% CI of sum(f)/sum(risked) by cluster bootstrap over games (all bets on one game move together)."""
    if not bets:
        return None
    n = n or PRE["boot_n"]
    by = {}
    for b in bets:
        a = by.setdefault(b["pid"], [0.0, 0.0])
        a[0] += f(b)
        a[1] += risked(b)
    cl = list(by.values())
    rng = random.Random(seed)
    xs = []
    for _ in range(n):
        smp = [cl[rng.randrange(len(cl))] for _ in cl]
        den = sum(s[1] for s in smp)
        if den > 0:
            xs.append(sum(s[0] for s in smp) / den)
    xs.sort()
    return [xs[int(0.025 * len(xs))], xs[int(0.975 * len(xs)) - 1]] if xs else None


def bucket(e):
    return "1-2c" if e < 0.02 else ("2-3c" if e < 0.03 else "3c+")


def summarize(bets, w):
    prim = [b for b in bets if is_primary(b)]
    settled = [b for b in bets if b["excluded"] is None and b["result"] in ("yes", "no")]
    out = {"bets": len(bets), "primary_bets": len(prim), "games": len({b["pid"] for b in bets}),
           "clv_per_dollar": roi(prim, clv), "clv_ci95": boot(prim, clv),
           "clv_proportional_per_dollar": roi([b for b in prim if b.get("fair_close_prop") is not None], clv_prop),
           "settled_bets": len(settled), "pnl_per_dollar": roi(settled, pnl), "pnl_ci95": boot(settled, pnl),
           "expected_per_dollar_at_entry": roi([b for b in bets if b["excluded"] is None], exp_at_entry),
           "dollars_risked": sum(risked(b) for b in bets if b["excluded"] is None),
           "voided": sum(1 for b in bets if b["result"] == "void"),
           "excluded": sum(1 for b in bets if b["excluded"] is not None)}
    if w:
        mid = w[0] + 7 * 86400
        out["clv_week1"] = roi([b for b in prim if b["entry_ts"] < mid], clv)
        out["clv_week2"] = roi([b for b in prim if b["entry_ts"] >= mid], clv)
    conv = [((b["start_bid"] + b["start_ask"]) / 2.0) - b["ask"] for b in bets
            if b["start_bid"] is not None and b["start_ask"] is not None and b["excluded"] is None]
    out["venue_convergence_cents"] = (100 * sum(conv) / len(conv)) if conv else None
    out["by_edge"] = {}
    for k in ("1-2c", "2-3c", "3c+"):
        bb = [b for b in bets if b["excluded"] is None and bucket(b["edge_entry"]) == k]
        out["by_edge"][k] = {"bets": len(bb), "clv_per_dollar": roi([b for b in bb if is_primary(b)], clv),
                             "pnl_per_dollar": roi([b for b in bb if b["result"] in ("yes", "no")], pnl)}
    out["by_sport"] = {}
    for s in C.SPORTS:
        bb = [b for b in bets if b["sport"] == s and b["excluded"] is None]
        if bb:
            out["by_sport"][s] = {"bets": len(bb), "clv_per_dollar": roi([b for b in bb if is_primary(b)], clv),
                                  "pnl_per_dollar": roi([b for b in bb if b["result"] in ("yes", "no")], pnl)}
    return out


def verdict(s):
    """PREREG verdict for one venue. Order: REJECT, TOO FEW, PASS, INCONCLUSIVE."""
    ci, pci = s.get("clv_ci95"), s.get("pnl_ci95")
    if (ci and ci[1] < 0) or (pci and pci[1] < 0):
        return "REJECT"
    if s["primary_bets"] < PRE["too_few_bets"]:
        return "TOO FEW"
    if (s["primary_bets"] >= PRE["pass_min_bets"] and ci and ci[0] > 0 and (s.get("clv_week1") or 0) > 0
            and (s.get("clv_week2") or 0) > 0 and s.get("pnl_per_dollar") is not None and s["pnl_per_dollar"] >= 0):
        return "PASS"
    return "INCONCLUSIVE"


def build(store, now=None):
    now = now or time.time()
    w = window(store)
    bets = store.rows("bets")
    out = {"prereg": C.PREREG, "version": C.VERSION, "now": now,
           "window": None if not w else [time.strftime("%Y-%m-%d", time.gmtime(w[0])),
                                         time.strftime("%Y-%m-%d", time.gmtime(w[1] - 1))]}
    inw = [b for b in bets if w and w[0] <= b["entry_ts"] < w[1]]
    out["bets_outside_window"] = len(bets) - len(inw)
    days = int(max(0, min(PRE["window_days"], (now - w[0]) // 86400))) if w else 0
    out["days_complete"] = days
    out["pending_settlement"] = sum(1 for b in inw if b["result"] is None and b["excluded"] is None)
    for v in VENUES:
        s = summarize([b for b in inw if b["venue"] == v], w)
        if w and now >= w[1] + 12 * 3600:
            s["verdict"] = verdict(s)
        else:
            s["verdict"] = f"IN PROGRESS ({days}/{PRE['window_days']} days)" if w else "window not started"
        out[v] = s
    out["removed_mismatches"] = [b["id"] for b in bets if (b["excluded"] or "").startswith("mismatch")]
    return out
