"""Pair venue games with Pinnacle events (PREREG Universe 2). Missing a match is fine; a wrong match is not, so the
rules are strict: both teams must match, orientation must be clear, start within 18h, women's and youth sides never
mix, and the venue game must pair with exactly one Pinnacle event."""
import re
import unicodedata
from collections import Counter

from . import teams as T
from .config import PRE

STOP = {"fc", "cf", "sc", "afc", "ac", "cd", "ud", "sd", "ssc", "as", "club", "the", "de", "of", "and", "cp", "sk",
        "if", "bk", "fk", "nk", "sv", "vfb", "vfl", "tsg", "rcd", "rc", "ca", "ce", "cs", "us"}
ABBR = {"utd": "united", "intl": "international", "univ": "university", "mt": "mount", "ft": "fort"}
MARKERS = {"w", "women", "womens", "ladies", "u17", "u18", "u19", "u20", "u21", "u23", "ii", "b", "reserves",
           "reserve", "youth", "academy", "fem", "femenino", "feminino", "feminine", "frauen"}
GENERIC = {"saint", "state", "university", "college", "united", "city", "real", "sporting", "athletic", "atletico",
           "deportivo", "la", "le", "el", "los", "las", "san", "santa", "new", "north", "south", "east", "west",
           "central", "northern", "southern", "eastern", "western", "a", "international", "town", "county", "rovers",
           "wanderers", "fort", "mount"}
WOMEN_RX = re.compile(r"\b(women|womens|ladies|wnba|nwsl|feminin\w*|femenin\w*|frauen)\b|\(w\)")


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[.'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def tokens(s):
    """(tokens, markers) for a team name."""
    raw = norm(s).split()
    out, marks = [], set()
    for i, w in enumerate(raw):
        if w == "st":
            w = "saint" if i == 0 else "state"
        w = ABBR.get(w, w)
        if w in MARKERS:
            marks.add("w" if w in ("w", "women", "womens", "ladies", "fem", "femenino", "feminino", "feminine",
                                   "frauen") else w)
            continue
        if w in STOP:
            continue
        out.append(w)
    return out, marks


def _tok_match(a, b, a_is_last):
    if a == b:
        return True
    if len(a) <= len(b):
        s, l, s_last = a, b, a_is_last
    else:
        return False
    if len(s) >= 3 and l.startswith(s):
        return True
    if len(s) == 1 and s_last and l.startswith(s):
        return True
    return False


def name_score(x, y):
    """Strict similarity in [0, 1]: matched tokens / the longer name's token count. Short tokens can match as
    prefixes ('man' ~ 'manchester', a final 'c' ~ 'clippers'). Different markers (women, U21, II) score 0, and a
    match made only of generic words ('state', 'united') scores 0."""
    A, ma = tokens(x)
    B, mb = tokens(y)
    if not A or not B or ma != mb:
        return 0.0
    short, long_ = (A, B) if len(A) <= len(B) else (B, A)
    used, matched, real = set(), 0, False
    for i, a in enumerate(short):
        for j, b in enumerate(long_):
            if j in used:
                continue
            if _tok_match(a, b, i == len(short) - 1) or _tok_match(b, a, j == len(long_) - 1):
                used.add(j)
                matched += 1
                if a not in GENERIC and b not in GENERIC:
                    real = True
                break
    if not real:
        return 0.0
    return matched / max(len(A), len(B))


def _alias_tables():
    out = {}
    for lg, rows in T.TABLES.items():
        cities = Counter(norm(c) for c, n, a in rows)
        m = {}

        def add(alias, canon):
            k = norm(alias)
            if not k:
                return
            if k in m and m[k] != canon:
                m[k] = None   # ambiguous inside this league
            else:
                m[k] = canon
        for c, n, a in rows:
            canon = f"{lg}:{a}"
            for al in (f"{c} {n}", n, a, f"{c} {n[0]}", f"{a} {n}"):
                add(al, canon)
            if cities[norm(c)] == 1:
                add(c, canon)
            if c == "Los Angeles":
                add("LA " + n, canon)
                add("LA " + n[0], canon)
            if c == "New York":
                add("NY " + n, canon)
            if c == "St. Louis":
                add("St Louis " + n, canon)
                add("Saint Louis " + n, canon)
                add("St Louis", canon)
                add("Saint Louis", canon)
        out[lg] = m
    out["NHL"][norm("Utah Hockey Club")] = "NHL:UTA"
    out["NHL"][norm("Utah HC")] = "NHL:UTA"
    return out


ALIAS = _alias_tables()


def canon(name, league):
    m = ALIAS.get(league or "")
    if not m:
        return None
    n = norm(name)
    return m.get(n)


def pin_league_code(league_name):
    """'NBA' / 'NHL' / 'NFL' when a Pinnacle league is the main pro league (not summer/G League/preseason)."""
    s = norm(league_name)
    toks = s.split()
    for lg in ("nba", "nhl", "nfl"):
        if lg in toks and not any(w in toks for w in ("summer", "g", "preseason", "pre", "women", "wnba")):
            return lg.upper()
    return None


def is_women(*texts):
    return any(WOMEN_RX.search((t or "").lower()) for t in texts)


def pair_score(game, ev):
    """(score, {venue team name: 'home'|'away'}) for one venue game vs one Pinnacle event."""
    a, b = game["teams"]
    lg = game.get("league_code")
    if lg and pin_league_code(ev["league"]) == lg:
        ca, cb, ch, cw = canon(a, lg), canon(b, lg), canon(ev["home"], lg), canon(ev["away"], lg)
        if None not in (ca, cb, ch, cw):
            if (ca, cb) == (ch, cw) and ca != cb:
                return 1.0, {a: "home", b: "away"}
            if (ca, cb) == (cw, ch) and ca != cb:
                return 1.0, {a: "away", b: "home"}
            return 0.0, None
    s1 = min(name_score(a, ev["home"]), name_score(b, ev["away"]))
    s2 = min(name_score(a, ev["away"]), name_score(b, ev["home"]))
    if s1 == s2:
        return 0.0, None          # can't tell which side is which: skip
    if s1 > s2:
        return s1, {a: "home", b: "away"}
    return s2, {a: "away", b: "home"}


def match_game(game, pin_events):
    """Returns (pinnacle event, side map, score, note). Event is None when there's no unique, clear match."""
    cands = []
    for ev in pin_events:
        if ev.get("parent_id"):
            continue
        if game.get("sport") and ev["sport"] != game["sport"]:
            continue
        if abs(ev["start_ts"] - game["ref_ts"]) > PRE["match_window"]:
            continue
        if is_women(ev["league"], ev["home"], ev["away"]) != game["women"]:
            continue
        sc, sides = pair_score(game, ev)
        if sides and sc >= PRE["match_min_score"]:
            cands.append((sc, ev, sides))
    if not cands:
        return None, None, 0.0, "no match"
    if len(cands) > 1:
        return None, None, max(c[0] for c in cands), f"ambiguous: {len(cands)} Pinnacle events"
    sc, ev, sides = cands[0]
    return ev, sides, sc, "ok"
