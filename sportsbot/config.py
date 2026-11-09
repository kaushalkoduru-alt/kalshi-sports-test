"""Settings. PRE holds every number PREREG.md fixes; it must not change during the test window.
Secrets and switches come from environment variables (Railway Variables)."""
import os
import secrets

VERSION = "0.3.1"
PREREG = "sports v1.2"

# pinnapi sport ids (PREREG: basketball, hockey, American football, soccer)
SPORTS = {"soccer": 1, "basketball": 3, "hockey": 4, "football": 5}
THREE_WAY = {"soccer"}

PRE = {
    "pin_budget_24h": 90,          # pinnapi requests per rolling 24h (free tier is 100/day)
    "prestart_window": 35 * 60,    # (a) a matched game starts within 35 min ...
    "prestart_gap": 30 * 60,       #     ... and no snapshot of that sport in the last 30 min
    "baseline_gap": 3 * 3600,      # (b) last snapshot 3h+ old ...
    "horizon": 24 * 3600,          #     ... and a matched game in the next 24h
    "min_lead": 10 * 60,           # game starts >= 10 min after the snapshot
    "venue_fresh": 120,            # venue price fetched within 120s of the Pinnacle snapshot
    "min_depth": 10,               # contracts at the best ask
    "max_size": 100,               # contracts per paper bet
    "edge_min": 0.01,              # fair - ask - fee >= 1 cent
    "fee_rate": {"kalshi": 0.07, "polyus": None},   # v1.1: Polymarket US uses each market's feeCoefficient
    "close_min_gap": 20 * 60,      # close taken >= 20 min after entry (primary sample)
    "close_max_before": 90 * 60,   # close taken no earlier than 90 min before the start
    "match_window": 18 * 3600,     # Pinnacle start within 18h of the venue's game time
    "match_min_score": 0.8,
    "window_days": 14,
    "boot_n": 10000,
    "pass_min_bets": 60,
    "too_few_bets": 30,
}

# operating cadence (plumbing, not test rules)
OPS = {
    "tick": 20,                    # main loop seconds
    "listing_every": 20 * 60,      # refresh venue game lists
    "series_every": 12 * 3600,     # refresh Kalshi's list of game series
    "settle_every": 15 * 60,
    "settle_after": 3 * 3600,      # start checking results 3h after the start
    "settle_giveup": 7 * 86400,
    "start_capture_lead": 90,      # capture venue mids this many seconds before a start
    "polyus_rps": 8.0,             # stay under Polymarket US's 25 req/s per IP
    "baseline_budget": 70,         # baseline/first-look snapshots stop at 70 of the 90, so pre-start ones go first
}

PINNAPI_KEY = os.environ.get("PINNAPI_KEY", "").strip()
DATA_DIR = os.environ.get("DATA_DIR", "/data")
PORT = int(os.environ.get("PORT", "8080"))
WINDOW_START = os.environ.get("WINDOW_START", "").strip()   # 'YYYY-MM-DD' (UTC), set once the code is verified
PROBE = os.environ.get("PROBE", "0").strip() == "1"           # collect and expose samples, no bets


def volume_ok():
    return os.path.ismount(DATA_DIR)


def _token(name):
    v = os.environ.get(name, "").strip()
    if v:
        return v
    path = os.path.join(DATA_DIR, "." + name.lower())
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        v = secrets.token_urlsafe(18)
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(path, "w") as f:
                f.write(v)
        except OSError:
            pass
        return v


READ_TOKEN = _token("READ_TOKEN")
