"""SQLite on the Railway volume. One connection guarded by one lock (the HTTP thread reads through it too)."""
import json
import os
import sqlite3
import threading
import time

from . import config as C

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS events (ts REAL, kind TEXT, msg TEXT);
CREATE TABLE IF NOT EXISTS pin_calls (ts REAL, sport TEXT, status TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS pin_snaps (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, sport TEXT, reason TEXT,
  n_events INTEGER, n_valid INTEGER);
CREATE TABLE IF NOT EXISTS pin_events (snap_id INTEGER, pid TEXT, sport TEXT, league TEXT, home TEXT, away TEXT,
  start_ts REAL, o_home REAL, o_away REAL, o_draw REAL, f_home REAL, f_away REAL, f_draw REAL, skip TEXT,
  PRIMARY KEY (snap_id, pid));
CREATE TABLE IF NOT EXISTS games (venue TEXT, gid TEXT, sport TEXT, league TEXT, title TEXT, ref_ts REAL,
  women INTEGER, data TEXT, pid TEXT, sides TEXT, start_ts REAL, match_score REAL, match_note TEXT, seen REAL,
  PRIMARY KEY (venue, gid));
CREATE TABLE IF NOT EXISTS quotes (snap_id INTEGER, venue TEXT, gid TEXT, okey TEXT, side TEXT, ts REAL, ask REAL,
  depth REAL, bid REAL, fee REAL, fair REAL, edge REAL, why TEXT, PRIMARY KEY (snap_id, venue, okey));
CREATE TABLE IF NOT EXISTS bets (id TEXT PRIMARY KEY, venue TEXT, gid TEXT, okey TEXT, side TEXT, pid TEXT,
  sport TEXT, league TEXT, title TEXT, outcome TEXT, start_ts REAL, entry_snap INTEGER, entry_ts REAL, ask REAL,
  n REAL, fee_total REAL, fair_entry REAL, edge_entry REAL, close_snap INTEGER, close_ts REAL, fair_close REAL,
  start_bid REAL, start_ask REAL, start_cap_ts REAL, result TEXT, settle_value REAL, settled_ts REAL,
  excluded TEXT);
CREATE TABLE IF NOT EXISTS probes (k TEXT PRIMARY KEY, ts REAL, body TEXT);
CREATE INDEX IF NOT EXISTS pin_events_pid ON pin_events (pid);
CREATE INDEX IF NOT EXISTS quotes_ts ON quotes (ts);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
"""


class Store:
    def __init__(self, path=None):
        self.path = path or os.path.join(C.DATA_DIR, "sports.db")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self._seen = {}

    def q(self, sql, args=()):
        with self.lock:
            return self.db.execute(sql, args).fetchall()

    def x(self, sql, args=()):
        with self.lock:
            cur = self.db.execute(sql, args)
            return cur.lastrowid

    def many(self, sql, rows):
        with self.lock:
            self.db.execute("BEGIN")
            try:
                self.db.executemany(sql, rows)
                self.db.execute("COMMIT")
            except Exception:
                self.db.execute("ROLLBACK")
                raise

    def meta(self, k, default=None):
        r = self.q("SELECT v FROM meta WHERE k=?", (k,))
        return json.loads(r[0][0]) if r else default

    def set_meta(self, k, v):
        self.x("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, json.dumps(v)))

    def event(self, kind, msg, dedupe=0):
        now = time.time()
        if dedupe:
            key = (kind, str(msg)[:60])
            if now - self._seen.get(key, 0) < dedupe:
                return
            self._seen[key] = now
        self.x("INSERT INTO events VALUES (?,?,?)", (now, kind, str(msg)[:2000]))
        print(f"[{kind}] {msg}", flush=True)

    def probe(self, k, body):
        self.x("INSERT INTO probes VALUES (?,?,?) ON CONFLICT(k) DO UPDATE SET ts=excluded.ts, body=excluded.body",
               (k, time.time(), json.dumps(body, default=str)[:3_000_000]))

    def cols(self, table):
        return [r[1] for r in self.q(f"PRAGMA table_info({table})")]

    def rows(self, table, where="", args=()):
        cs = self.cols(table)
        return [dict(zip(cs, r)) for r in self.q(f"SELECT * FROM {table} {where}", args)]
