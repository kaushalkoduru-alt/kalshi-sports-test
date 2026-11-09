"""HTTP API on Railway's public URL. Everything but /health needs ?t=READ_TOKEN. Read-only: there is nothing to
trade, so there is no kill switch."""
import hmac
import json
import threading
import time
import urllib.parse
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import config as C
from . import net, pinnacle, report

TABLES = {"bets", "quotes", "pin_snaps", "pin_events", "pin_calls", "games", "events", "meta"}


def status(state):
    st, eng = state["store"], state.get("engine")
    now = time.time()
    games = eng.games() if eng else []
    by = {}
    for g in games:
        d = by.setdefault(g["venue"], {"games": 0, "matched": 0, "notes": Counter()})
        d["games"] += 1
        d["matched"] += 1 if g["pid"] else 0
        d["notes"][g["match_note"] or "not tried yet"] += 1
    for d in by.values():
        d["notes"] = dict(d["notes"])
    bets = st.q("SELECT COUNT(*), SUM(result IS NOT NULL), SUM(close_ts IS NOT NULL) FROM bets")[0]
    return {"version": C.VERSION, "prereg": C.PREREG, "now": now, "probe": C.PROBE, "pinnapi_key": bool(C.PINNAPI_KEY),
            "volume_mounted": C.volume_ok(), "window": report.window(st) and [time.strftime(
                "%Y-%m-%d", time.gmtime(x)) for x in report.window(st)],
            "last_tick_age": round(now - state["last_tick"], 1) if state.get("last_tick") else None,
            "pinnapi_calls_24h": pinnacle.calls_last_24h(st, now), "budget_24h": C.PRE["pin_budget_24h"],
            "snaps": eng.status["snaps"] if eng else {}, "skipped": eng.status["skipped"] if eng else {},
            "listing": eng.status["listing"] if eng else {}, "matching": by,
            "due": eng.due() if eng else [],
            "bets": {"total": bets[0] or 0, "settled": bets[1] or 0, "with_close": bets[2] or 0},
            "http": net.STATS,
            "events": st.q("SELECT ts, kind, msg FROM events ORDER BY ts DESC LIMIT 40")}


def matches(state):
    eng, st = state.get("engine"), state["store"]
    out = []
    for g in eng.games() if eng else []:
        ev = None
        if g["pid"]:
            for s in eng.pin_latest.values():
                if g["pid"] in s:
                    ev = s[g["pid"]]
        out.append({"venue": g["venue"], "gid": g["gid"], "sport": g["sport"], "league": g["league"],
                    "title": g["title"], "teams": g["teams"], "ref_ts": g["ref_ts"], "note": g["match_note"],
                    "score": g["match_score"], "sides": g["sides"],
                    "pinnacle": ev and {"pid": ev["pid"], "league": ev["league"], "home": ev["home"],
                                        "away": ev["away"], "start_ts": ev["start_ts"], "fair": ev["fair"]}})
    return out


def serve(state):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            body = json.dumps(obj, default=str).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            if u.path == "/health":
                lt = state.get("last_tick")
                age = (time.time() - lt) if lt else None
                return self._send(200, {"ok": age is not None and age < 300, "starting": lt is None,
                                        "version": C.VERSION, "last_tick_age": age and round(age, 1),
                                        "probe": C.PROBE})
            if not hmac.compare_digest(q.get("t", ""), C.READ_TOKEN):
                return self._send(403, {"error": "bad token"})
            st = state["store"]
            try:
                if u.path == "/status":
                    return self._send(200, status(state))
                if u.path == "/report":
                    return self._send(200, report.build(st))
                if u.path == "/matches":
                    return self._send(200, matches(state))
                if u.path == "/probe":
                    k = q.get("k")
                    if k:
                        r = st.q("SELECT ts, body FROM probes WHERE k=?", (k,))
                        return self._send(200, {"ts": r[0][0], "body": json.loads(r[0][1])} if r else {"error": "none"})
                    return self._send(200, {"keys": [r[0] for r in st.q("SELECT k FROM probes ORDER BY k")]})
                if u.path == "/export":
                    t = q.get("table", "")
                    if t not in TABLES:
                        return self._send(400, {"error": "unknown table"})
                    lim = max(1, min(int(q.get("limit", 20000)), 100000))
                    since = float(q.get("since", 0))
                    col = {"bets": "entry_ts", "quotes": "snap_id", "pin_snaps": "ts", "pin_calls": "ts",
                           "events": "ts", "pin_events": "snap_id"}.get(t)
                    sql = f"SELECT * FROM {t}" + (f" WHERE {col} >= ? ORDER BY {col}" if col else "") + f" LIMIT {lim}"
                    return self._send(200, {"cols": st.cols(t), "rows": st.q(sql, (since,) if col else ())})
            except Exception as e:      # never take the loop down from a read
                return self._send(500, {"error": str(e)})
            return self._send(404, {"error": "not found"})

    srv = ThreadingHTTPServer(("0.0.0.0", C.PORT), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
