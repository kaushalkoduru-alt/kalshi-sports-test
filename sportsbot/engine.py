"""The test loop (PREREG Snapshots, Entry rule, Metrics inputs): venue listings, matching, budgeted Pinnacle
snapshots, venue quotes, paper bets, closes, start prices, settlement."""
import json
import math
import time
import traceback

from . import config as C
from . import kalshi, match, net, pinnacle, polyus, report

PRE, OPS = C.PRE, C.OPS


def ceil_cent(x):
    return math.ceil(round(x * 100, 6)) / 100.0


def bankers_cent(x):
    from decimal import ROUND_HALF_EVEN, Decimal
    return float(Decimal(repr(round(x, 10))).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def fee_per_contract(venue, p, coef=None):
    """Taker fee per contract at price p. Kalshi: 0.07 x p x (1-p). Polymarket US: the market's own
    feeCoefficient x p x (1-p) (PREREG amendment v1.1); None when the market doesn't report one."""
    rate = PRE["fee_rate"][venue] if venue == "kalshi" else coef
    if rate is None:
        return None
    return rate * p * (1.0 - p)


def fee_total(venue, p, n, coef=None):
    f = fee_per_contract(venue, p, coef) * n
    return ceil_cent(f) if venue == "kalshi" else bankers_cent(f)


def plog(tag, obj, limit=3000):
    """Print a compact line to the Railway logs (the read channel when the service URL isn't reachable)."""
    print(f"{tag} {json.dumps(obj, default=str, separators=(',', ':'))[:limit]}", flush=True)


def _compact_pm_event(e):
    return {"id": e.get("id"), "slug": e.get("slug"), "title": e.get("title"), "startTime": e.get("startTime"),
            "endTime": e.get("endTime"), "active": e.get("active"), "closed": e.get("closed"),
            "tags": [t.get("slug") for t in e.get("tags") or []], "series": (e.get("series") or {}).get("slug"),
            "other_keys": sorted(k for k in e if k not in ("markets", "tags", "series")),
            "markets": [{k: m.get(k) for k in ("slug", "title", "outcome", "active", "closed")} | {
                "keys": sorted(m.keys())} for m in (e.get("markets") or [])[:4]]}


class Engine:
    def __init__(self, store, kal=kalshi, pmu=polyus, pin=pinnacle, clock=time.time):
        self.st, self.kal, self.pmu, self.pin, self.now = store, kal, pmu, pin, clock
        self.series, self.series_ts, self.listing_ts, self.settle_ts = {}, 0.0, 0.0, 0.0
        self.pin_latest = {s: {} for s in C.SPORTS}
        self.status = {"listing": {}, "snaps": {}, "skipped": store.meta("skipped", {})}
        self._held = {}
        if not store.meta("pin_events_mains_only"):
            # before v0.2.1 special rows were stored too and came back as games after a restart (probe data only,
            # nothing was ever bet on it): start the Pinnacle side clean
            store.x("DELETE FROM pin_events")
            store.x("DELETE FROM pin_snaps")
            store.set_meta("pin_events_mains_only", True)
        self._load_latest()

    # ------------------------------------------------------------------ state
    def _load_latest(self):
        for sport in C.SPORTS:
            r = self.st.q("SELECT id, ts, reason, n_events, n_valid FROM pin_snaps WHERE sport=? ORDER BY id DESC "
                          "LIMIT 1", (sport,))
            if not r:
                continue
            self.status["snaps"][sport] = {"ts": r[0][1], "reason": r[0][2], "events": r[0][3], "valid": r[0][4]}
            evs = {}
            for row in self.st.rows("pin_events", "WHERE snap_id=?", (r[0][0],)):
                ev = {"pid": row["pid"], "sport": sport, "league": row["league"], "home": row["home"],
                      "away": row["away"], "start_ts": row["start_ts"], "parent_id": None,
                      "odds": {"home": row["o_home"], "away": row["o_away"], "draw": row["o_draw"]},
                      "fair": None, "fair_prop": None, "fair_pow": None, "skip": row["skip"]}
                if row["f_home"] is not None:      # stored fairs are v1 proportional; both de-vigs come from the odds
                    pinnacle.fairs(ev, sport)
                evs[row["pid"]] = ev
            self.pin_latest[sport] = evs

    def games(self, venue=None, current=True):
        where, args = [], []
        if venue:
            where.append("venue=?")
            args.append(venue)
        if current:
            where.append("seen >= ?")
            args.append(self.listing_ts - 1)
        rows = self.st.rows("games", ("WHERE " + " AND ".join(where)) if where else "", tuple(args))
        for g in rows:
            g.update(json.loads(g.pop("data")))
            g["sides"] = json.loads(g["sides"]) if g["sides"] else None
        return rows

    def last_snap(self, sport):
        r = self.st.q("SELECT MAX(ts) FROM pin_snaps WHERE sport=?", (sport,))
        return r[0][0] if r and r[0][0] else None

    # ------------------------------------------------------------------ listings and matching
    def refresh_listings(self):
        now = self.now()
        if not self.series or now - self.series_ts > OPS["series_every"]:
            self.series = self.kal.game_series()
            self.series_ts = now
        kg = self.kal.open_games(self.series)
        raw = [] if C.PROBE else None
        try:
            pg, odd = self.pmu.games(now - 6 * 3600, now + 4 * 86400, raw_out=raw) if C.PROBE else \
                self.pmu.games(now - 6 * 3600, now + 4 * 86400)
        except net.HTTPFail as e:
            pg, odd = [], []
            self.st.event("error", f"polymarket us listing failed: {e} {str(e.body or '')[:300]}", dedupe=1800)
        if C.PROBE:
            self._probe_polyus(raw or [], pg)
        rows = []
        for g in kg + pg:
            data = {k: g[k] for k in ("teams", "outcomes", "league_code", "series") if k in g}
            rows.append((g["venue"], g["gid"], g["sport"], g["league"], g["title"], g["ref_ts"], int(g["women"]),
                         json.dumps(data), now))
        self.st.many("INSERT INTO games (venue, gid, sport, league, title, ref_ts, women, data, seen) "
                     "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(venue, gid) DO UPDATE SET sport=excluded.sport, "
                     "league=excluded.league, title=excluded.title, ref_ts=excluded.ref_ts, women=excluded.women, "
                     "data=excluded.data, seen=excluded.seen", rows)
        self.listing_ts = now
        self.status["listing"] = {"ts": now, "kalshi_games": len(kg), "polyus_games": len(pg),
                                  "polyus_unparsed": len(odd), "kalshi_series": len(self.series)}
        if C.PROBE:
            self.st.probe("kalshi_games_sample", kg[:15])
            self.st.probe("polyus_games_sample", pg[:15])
            self.st.probe("polyus_unparsed_slugs", odd[:60])
            from collections import Counter
            plog("PROBE kalshi_counts", {"games": len(kg), "series": len(self.series),
                                         "by_sport": Counter(g["sport"] for g in kg),
                                         "women": sum(1 for g in kg if g["women"])})
            for i, g in enumerate([g for g in kg if g["sport"] == s][:1] for s in C.SPORTS):
                if g:
                    plog(f"PROBE kalshi_game_{i}", g[0])
            plog("PROBE pm_parsed", {"games": len(pg), "unparsed": len(odd), "sample": pg[:3],
                                     "unparsed_sample": odd[:20]})
        self.rematch(self.games())
        if C.PROBE:
            for s in C.SPORTS:
                if self.pin_latest[s]:
                    self._audit(s, venue="polyus")

    def _probe_polyus(self, raw, parsed):
        """Probe mode only: raw Polymarket US payloads so the parser can be checked against reality."""
        from collections import Counter
        tags = Counter(t.get("slug") for e in raw for t in (e.get("tags") or []))
        series = Counter((e.get("series") or {}).get("slug") for e in raw)
        nmk = Counter(len(e.get("markets") or []) for e in raw)
        self.st.probe("polyus_raw_events", {"n": len(raw), "tags": tags.most_common(80),
                                            "series": series.most_common(80), "markets_per_event": dict(nmk),
                                            "sample": raw[:6]})
        plog("PROBE pm_counts", {"events": len(raw), "parsed_games": len(parsed), "markets_per_event": dict(nmk),
                                 "tags": tags.most_common(40), "series": series.most_common(40)})
        smt = Counter(str(m.get("sportsMarketTypeV2") or m.get("sportsMarketType")) for e in raw
                      for m in (e.get("markets") or []))
        fees = Counter((str(m.get("sportsMarketTypeV2")), m.get("feeCoefficient")) for e in raw
                       for m in (e.get("markets") or []))
        plog("PROBE pm_market_types", {"types": smt.most_common(30), "fee_by_type": [
            [k[0], k[1], n] for k, n in fees.most_common(30)]})
        shown = Counter()
        for e in raw:
            tg = [t.get("slug") for t in e.get("tags") or []]
            key = next((x for x in ("nba", "nhl", "nfl", "cfb", "ncaaf", "cbb", "ncaab", "soccer") if x in tg), None)
            if not key or shown[key] >= 2 or not any("MONEYLINE" in str(m.get("sportsMarketTypeV2") or "").upper()
                                                     or len(e.get("markets") or []) <= 3 for m in e.get("markets") or []):
                continue
            shown[key] += 1
            ml = [m for m in e.get("markets") or [] if "MONEYLINE" in str(m.get("sportsMarketTypeV2") or "").upper()]
            mk = (ml or e.get("markets") or [])[:3]
            plog(f"PROBE pm_game_{key}_{shown[key]}", {
                "slug": e.get("slug"), "title": e.get("title"), "startTime": e.get("startTime"), "tags": tg,
                "teams": e.get("teams"), "participants": e.get("participants"), "n_markets": len(e.get("markets") or []),
                "markets": [{k: m.get(k) for k in ("slug", "title", "titleShort", "question", "sportsMarketType",
                                                   "sportsMarketTypeV2", "marketType", "outcomes", "outcomePrices",
                                                   "feeCoefficient", "marketSides", "gameStartTime", "status")}
                            for m in mk]}, limit=6000)
        sports_like = [e for e in raw if len(e.get("markets") or []) in (2, 3)][:3] or raw[:3]
        for i, e in enumerate(sports_like):
            plog(f"PROBE pm_event_{i}", _compact_pm_event(e))
        for k, path, params in (("polyus_raw_sports", "/v1/sports", None),
                                ("polyus_raw_unfiltered", "/v1/events", {"limit": 15, "active": "true"})):
            try:
                body = self.pmu.raw(path, params)
            except Exception as e:
                body = {"error": str(e), "body": str(getattr(e, "body", ""))[:500]}
            self.st.probe(k, body)
            if k == "polyus_raw_unfiltered" and isinstance(body, dict) and body.get("events"):
                plog("PROBE pm_unfiltered", {"n": len(body["events"]),
                                             "first": _compact_pm_event(body["events"][0])})
            else:
                plog(f"PROBE {k}", body)
        slugs = [m.get("slug") for e in sports_like for m in (e.get("markets") or [])][:2]
        out = {}
        for s in slugs:
            for kind, path in (("bbo", f"/v1/markets/{s}/bbo"), ("book", f"/v1/markets/{s}/book"),
                               ("market", f"/v1/market/slug/{s}"), ("settlement", f"/v1/markets/{s}/settlement")):
                try:
                    out[f"{kind}:{s}"] = self.pmu.raw(path)
                except Exception as e:
                    out[f"{kind}:{s}"] = {"error": str(e), "body": str(getattr(e, 'body', ''))[:300]}
                plog(f"PROBE pm_{kind}", {"slug": s, "body": out[f"{kind}:{s}"]}, limit=2000)
        self.st.probe("polyus_raw_market", out)

    def rematch(self, games):
        rows = []
        for g in games:
            sports = [g["sport"]] if g["sport"] else list(C.SPORTS)
            evs = [e for s in sports for e in self.pin_latest[s].values()]
            if not evs:
                continue
            ev, sides, sc, note = match.match_game(
                {"teams": g["teams"], "sport": g["sport"], "league_code": g.get("league_code"), "women": bool(g["women"]),
                 "ref_ts": g["ref_ts"]}, evs)
            if ev:
                rows.append((ev["pid"], json.dumps(sides), ev["start_ts"], sc, note, g["venue"], g["gid"]))
            else:
                rows.append((None, None, None, sc, note, g["venue"], g["gid"]))
        if rows:
            self.st.many("UPDATE games SET pid=?, sides=?, start_ts=?, match_score=?, match_note=? "
                         "WHERE venue=? AND gid=?", rows)

    # ------------------------------------------------------------------ scheduler (PREREG Snapshots)
    def due(self):
        now = self.now()
        out = []
        gs = self.games()
        for sport in C.SPORTS:
            last = self.last_snap(sport)
            matched = [g for g in gs if g["pid"] and g["start_ts"] and g["start_ts"] > now and
                       self._sport_of_pid(g["pid"]) == sport]
            if any(g["start_ts"] - now <= PRE["prestart_window"] for g in matched) and \
                    (last is None or now - last >= PRE["prestart_gap"]):
                out.append((0, sport, "prestart"))
            elif any(g["start_ts"] - now <= PRE["horizon"] for g in matched) and \
                    (last is None or now - last >= PRE["baseline_gap"]):
                out.append((1, sport, "baseline"))
            elif (last is None or now - last >= PRE["horizon"]) and \
                    any((g["sport"] in (sport, None)) and 0 < g["ref_ts"] - now <= PRE["horizon"] + PRE["match_window"]
                        for g in gs):
                out.append((2, sport, "bootstrap"))     # first look: venue games exist but nothing matched yet
        return sorted(out)

    def _sport_of_pid(self, pid):
        for s, evs in self.pin_latest.items():
            if pid in evs:
                return s
        return None

    # ------------------------------------------------------------------ one snapshot
    def _skip(self, sport, reason, msg):
        """Count a skipped snapshot once per sport and reason per 30 minutes (due() keeps re-asking)."""
        now = self.now()
        if now - self._held.get((sport, reason), 0) < 1800:
            return
        self._held[(sport, reason)] = now
        sk = self.status["skipped"]
        sk[reason] = sk.get(reason, 0) + 1
        self.st.set_meta("skipped", sk)
        self.st.event("budget", msg, dedupe=1800)

    def snapshot(self, sport, reason):
        if reason != "prestart" and self.pin.calls_last_24h(self.st, self.now()) >= OPS["baseline_budget"]:
            self._skip(sport, reason, f"held back a {reason} snapshot of {sport}: the rest is saved for pre-start "
                                      f"snapshots")
            return None
        try:
            payload = self.pin.fetch(self.st, sport, reason)
        except self.pin.BudgetExhausted as e:
            self._skip(sport, reason, f"skipped a {reason} snapshot of {sport}: {e}")
            return None
        except net.HTTPFail as e:
            self.st.event("error", f"pinnapi {sport}: {e} {str(e.body or '')[:200]}", dedupe=600)
            return None
        snap_ts = self.now()
        evs = self.pin.parse(payload, sport)
        valid = [e for e in evs if e["fair"]]
        snap_id = self.st.x("INSERT INTO pin_snaps (ts, sport, reason, n_events, n_valid) VALUES (?,?,?,?,?)",
                            (snap_ts, sport, reason, len(evs), len(valid)))
        # special rows (corners, bookings: parent_id set) are never stored, so a restart can't mistake them for games
        self.st.many("INSERT OR REPLACE INTO pin_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            (snap_id, e["pid"], sport, e["league"], e["home"], e["away"], e["start_ts"], e["odds"]["home"],
             e["odds"]["away"], e["odds"]["draw"], (e["fair_prop"] or {}).get("home"),
             (e["fair_prop"] or {}).get("away"), (e["fair_prop"] or {}).get("draw"), e["skip"])
            for e in evs if not e.get("parent_id")])     # f_* columns hold the v1 proportional fair
        self.pin_latest[sport] = {e["pid"]: e for e in evs if not e.get("parent_id")}
        self.status["snaps"][sport] = {"ts": snap_ts, "reason": reason, "events": len(evs), "valid": len(valid)}
        if C.PROBE:
            from collections import Counter
            raw = (payload or {}).get("events") if isinstance(payload, dict) else payload
            if isinstance(raw, dict):
                raw = list(raw.values())
            self.st.probe(f"pin_raw_{sport}", {"top_keys": list(payload.keys()) if isinstance(payload, dict) else None,
                                                "sample": (raw or [])[:4],
                                                "leagues": sorted({e["league"] for e in evs})[:300]})
            plog(f"PROBE pin_{sport}", {"top_keys": list(payload.keys()) if isinstance(payload, dict) else
                                        type(payload).__name__, "events": len(evs), "valid": len(valid),
                                        "skips": Counter(e["skip"] for e in evs),
                                        "leagues": sorted({e["league"] for e in evs})[:70]})
            for i, r in enumerate((raw or [])[:2]):
                plog(f"PROBE pin_{sport}_raw_{i}", r, limit=2500)
        self.rematch([g for g in self.games() if g["sport"] in (sport, None)])
        if C.PROBE:
            self._audit(sport)
        n_bets = self._quote_and_bet(sport, snap_id, snap_ts)
        self._closes(sport, snap_id, snap_ts)
        return {"snap_id": snap_id, "events": len(evs), "valid": len(valid), "bets": n_bets}

    def _audit(self, sport, venue=None):
        """Probe mode: matched pairs and unmatched games, for checking the matcher by hand."""
        from collections import Counter
        evs = self.pin_latest[sport]
        gs = [g for g in self.games() if g["sport"] in (sport, None) and (venue is None or g["venue"] == venue)]
        tag = f"{sport}_{venue}" if venue else sport
        plog(f"PROBE match_counts_{tag}", dict(Counter(f"{g['venue']}:{'matched' if g['pid'] else g['match_note']}"
                                                       for g in gs)))
        sport = tag
        pairs = []
        for g in gs:
            ev = evs.get(g["pid"]) if g["pid"] else None
            if ev:
                pairs.append([g["venue"], g["teams"], ev["home"], ev["away"], ev["league"], g["sides"],
                              round(g["match_score"], 2)])
        for i in range(0, min(len(pairs), 40), 10):
            plog(f"PROBE matched_{sport}_{i // 10}", pairs[i:i + 10])
        un = [[g["venue"], g["league"], g["teams"], g["match_note"]] for g in gs if not g["pid"]]
        for i in range(0, min(len(un), 40), 20):
            plog(f"PROBE unmatched_{sport}_{i // 20}", un[i:i + 20])

    def summary(self):
        """Compact status + report line for the logs (and the daily digest)."""
        from collections import Counter
        from . import report
        now = self.now()
        m = Counter(f"{g['venue']}:{'matched' if g['pid'] else 'unmatched'}" for g in self.games())
        b = self.st.q("SELECT COUNT(*), SUM(result IS NOT NULL), SUM(close_ts IS NOT NULL) FROM bets")[0]
        errs = self.st.q("SELECT COUNT(*) FROM events WHERE kind IN ('error','watchdog') AND ts > ?",
                         (now - 86400,))[0][0]
        r = report.build(self.st, now)
        rep = {"window": r["window"], "days": r["days_complete"], "pending_settlement": r["pending_settlement"],
               "bets_outside_window": r["bets_outside_window"]}
        for v in ("kalshi", "polyus"):
            s = r[v]
            rep[v] = {k: s.get(k) for k in ("bets", "primary_bets", "clv_per_dollar", "clv_ci95",
                                           "clv_proportional_per_dollar", "settled_bets", "pnl_per_dollar",
                                           "pnl_ci95", "verdict")}
        return {"v": C.VERSION, "probe": C.PROBE, "key": bool(C.PINNAPI_KEY), "volume": C.volume_ok(),
                "calls_24h": self.pin.calls_last_24h(self.st, now),
                "snaps": {s: [round((now - x["ts"]) / 60), x["reason"], x["valid"]] for s, x in
                          self.status["snaps"].items()},
                "skipped": self.status["skipped"], "listing": self.status["listing"], "matching": dict(m),
                "bets": {"total": b[0] or 0, "settled": b[1] or 0, "with_close": b[2] or 0},
                "errors_24h": errs, "report": rep}

    def _quote_and_bet(self, sport, snap_id, snap_ts):
        evs = self.pin_latest[sport]
        targets = []
        for g in self.games():
            ev = evs.get(g["pid"]) if g["pid"] else None
            if not ev or not ev["fair"] or ev["start_ts"] - snap_ts < PRE["min_lead"]:
                continue
            targets.append((g, ev))
        kt = [o["key"] for g, ev in targets if g["venue"] == "kalshi" for o in g["outcomes"]]
        pt = [o["key"] for g, ev in targets if g["venue"] == "polyus" for o in g["outcomes"]]
        quotes = {}
        try:
            quotes.update({("kalshi", k): v for k, v in self.kal.books(kt).items()})
        except net.HTTPFail as e:
            self.st.event("error", f"kalshi books: {e}", dedupe=600)
        try:
            quotes.update({("polyus", k): v for k, v in self.pmu.books(pt).items()})
        except net.HTTPFail as e:
            self.st.event("error", f"polymarket us books: {e}", dedupe=600)
        rows, n_bets = [], 0
        for g, ev in targets:
            for o in g["outcomes"]:
                side = "draw" if o["kind"] == "draw" else (g["sides"] or {}).get(o["name"])
                fair = ev["fair"].get(side) if side else None
                q = quotes.get((g["venue"], o["key"]))
                why, ask, depth, bid, qts, fee, edge = None, None, None, None, None, None, None
                if fair is None:
                    why = "no fair price for this outcome"
                elif not q:
                    why = "no venue quote"
                else:
                    ask, depth, bid, qts = q.get("ask"), q.get("depth") or 0.0, q.get("bid"), q.get("ts")
                    if qts is None or abs(qts - snap_ts) > PRE["venue_fresh"]:
                        why = "venue price too old"
                    elif ask is None or not 0 < ask < 1:
                        why = "no ask"
                    elif depth < PRE["min_depth"]:
                        why = "under 10 contracts at the ask"
                    elif fee_per_contract(g["venue"], ask, o.get("fee")) is None:
                        why = "no fee coefficient"
                    else:
                        fee = fee_per_contract(g["venue"], ask, o.get("fee"))
                        edge = fair - ask - fee
                rows.append((snap_id, g["venue"], g["gid"], o["key"], side, qts, ask, depth, bid, fee, fair, edge, why))
                if why is None and edge >= PRE["edge_min"] - 1e-12 and not C.PROBE:
                    if self._bet(g, ev, o, side, snap_id, snap_ts, ask, depth, fair, edge):
                        n_bets += 1
        if rows:
            self.st.many("INSERT OR REPLACE INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
            self._audit_quotes(sport, snap_id, rows, targets)
        return n_bets

    def _audit_quotes(self, sport, snap_id, rows, targets):
        """One log line per snapshot: why outcomes weren't compared, and the biggest gaps (a gap over 10c is far more
        likely a bad match or a stale book than a real edge, so it's flagged for a look)."""
        from collections import Counter
        title = {(g["venue"], g["gid"]): g["title"] for g, _ in targets}
        why = Counter(r[12] or "compared" for r in rows)
        top = sorted((r for r in rows if r[11] is not None), key=lambda r: -r[11])[:6]
        plog("AUDIT quotes", {"sport": sport, "snap": snap_id, "outcomes": len(rows), "why": dict(why),
                              "by_venue": dict(Counter(r[1] for r in rows if r[11] is not None)),
                              "top": [[r[1], title.get((r[1], r[2]), r[2])[:40], r[4], r[6], round(r[10], 3),
                                       round(r[11] * 100, 1), "CHECK" if r[11] > 0.10 else ""] for r in top]})

    def _bet(self, g, ev, o, side, snap_id, snap_ts, ask, depth, fair, edge):
        # one bet per (game, venue, outcome) inside the window. A bet entered outside it (verification before the window
        # opens, even after WINDOW_START is set) keeps the plain id, so it never uses up the window's one bet.
        w = report.window(self.st)
        inside = bool(w) and w[0] <= snap_ts < w[1]
        bid_id = (f"w{time.strftime('%Y-%m-%d', time.gmtime(w[0]))}:" if inside else "") + f"{g['venue']}:{o['key']}"
        if self.st.q("SELECT 1 FROM bets WHERE id=?", (bid_id,)):
            return False
        n = float(min(PRE["max_size"], math.floor(depth)))
        fee_tot = fee_total(g["venue"], ask, n, o.get("fee"))
        fprop = (ev.get("fair_prop") or {}).get(side)     # v1 proportional fair, kept for the secondary metric
        close = (snap_id, snap_ts, fair, fprop) if ev["start_ts"] - snap_ts <= PRE["close_max_before"] else \
            (None, None, None, None)
        self.st.x("INSERT INTO bets (id, venue, gid, okey, side, pid, sport, league, title, outcome, start_ts, "
                  "entry_snap, entry_ts, ask, n, fee_total, fair_entry, edge_entry, close_snap, close_ts, fair_close, "
                  "fair_close_prop, fair_entry_prop) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (bid_id, g["venue"], g["gid"], o["key"], side, ev["pid"], ev["sport"], ev["league"], g["title"],
                   o["name"], ev["start_ts"], snap_id, snap_ts, ask, n, fee_tot, fair, edge, *close, fprop))
        self.st.event("bet", f"{bid_id} | {g['title']} | {o['name']} at {ask:.2f} vs fair {fair:.3f} "
                             f"(proportional {'-' if fprop is None else f'{fprop:.3f}'}) "
                             f"(edge {edge * 100:.1f}c, {int(n)} contracts, fee ${fee_tot:.2f}) pin {ev['pid']} "
                             f"start {time.strftime('%m-%d %H:%M', time.gmtime(ev['start_ts']))}Z")
        return True

    def _closes(self, sport, snap_id, snap_ts):
        """Close = the sport's last snapshot before the start (that still lists the game), no earlier than 90 min
        before it. Later snapshots overwrite earlier ones; the start time follows Pinnacle's latest."""
        evs = self.pin_latest[sport]
        upd = []
        for b in self.st.rows("bets", "WHERE sport=? AND excluded IS NULL AND start_ts > ?", (sport, snap_ts)):
            ev = evs.get(b["pid"])
            if not ev or not ev["fair"] or ev["fair"].get(b["side"]) is None:
                continue
            if ev["start_ts"] and abs(ev["start_ts"] - b["start_ts"]) > 1:
                self.st.x("UPDATE bets SET start_ts=? WHERE id=?", (ev["start_ts"], b["id"]))
            if ev["start_ts"] - snap_ts <= PRE["close_max_before"]:
                self.st.x("UPDATE bets SET close_snap=?, close_ts=?, fair_close=?, fair_close_prop=? WHERE id=?",
                          (snap_id, snap_ts, ev["fair"][b["side"]], (ev.get("fair_prop") or {}).get(b["side"]),
                           b["id"]))
                upd.append([b["id"], round(ev["fair"][b["side"]], 4), round((ev["start_ts"] - snap_ts) / 60),
                            round((snap_ts - b["entry_ts"]) / 60)])
        if upd:
            plog("CLOSES", {"sport": sport, "snap": snap_id, "bets": upd})   # [id, fair close, min to start, min after entry]

    # ------------------------------------------------------------------ start prices and settlement
    def capture_starts(self):
        now = self.now()
        bs = self.st.rows("bets", "WHERE start_cap_ts IS NULL AND excluded IS NULL AND start_ts - ? <= ? "
                                  "AND start_ts > ?", (now, OPS["start_capture_lead"], now - 120))
        if not bs:
            return
        kq, pq = {}, {}
        try:
            kq = self.kal.books([b["okey"] for b in bs if b["venue"] == "kalshi"])
        except net.HTTPFail as e:
            self.st.event("error", f"kalshi start prices: {e}", dedupe=600)
        try:
            pq = self.pmu.books([b["okey"] for b in bs if b["venue"] == "polyus"])
        except net.HTTPFail as e:
            self.st.event("error", f"polymarket us start prices: {e}", dedupe=600)
        for b in bs:
            q = (kq if b["venue"] == "kalshi" else pq).get(b["okey"])
            if q:
                self.st.x("UPDATE bets SET start_bid=?, start_ask=?, start_cap_ts=? WHERE id=?",
                          (q.get("bid"), q.get("ask"), q.get("ts"), b["id"]))
                self.st.event("start", f"{b['id']} venue bid {q.get('bid')} ask {q.get('ask')} (entry ask {b['ask']})")

    def settle(self):
        now = self.now()
        for b in self.st.rows("bets", "WHERE result IS NULL AND excluded IS NULL AND start_ts < ?",
                              (now - OPS["settle_after"],)):
            try:
                res, val = (self.kal if b["venue"] == "kalshi" else self.pmu).result(b["okey"])
            except net.HTTPFail as e:
                self.st.event("error", f"result {b['id']}: {e}", dedupe=1800)
                continue
            if res:
                self.st.x("UPDATE bets SET result=?, settle_value=?, settled_ts=? WHERE id=?", (res, val, now, b["id"]))
                self.st.event("settle", f"{b['id']} {res} ({b['outcome']}, entry ask {b['ask']})")
            elif now - b["start_ts"] > OPS["settle_giveup"]:
                self.st.x("UPDATE bets SET excluded=? WHERE id=?", ("unsettled after 7 days", b["id"]))

    # ------------------------------------------------------------------ main tick
    def tick(self):
        now = self.now()
        if now - self.listing_ts >= OPS["listing_every"]:
            try:
                self.refresh_listings()
            except Exception as e:
                self.st.event("error", f"listing refresh: {e}\n{traceback.format_exc()[-800:]}", dedupe=600)
                self.listing_ts = now - OPS["listing_every"] + 120     # retry in 2 minutes
        for _, sport, reason in self.due():
            self.snapshot(sport, reason)
        self.capture_starts()
        if now - self.settle_ts >= OPS["settle_every"]:
            self.settle_ts = now
            self.settle()
