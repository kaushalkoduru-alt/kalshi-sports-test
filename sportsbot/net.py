"""Tiny JSON-over-HTTPS helper (stdlib only) with retries and per-host counters."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

UA = "kalshi-sports-test/0.1 (paper research)"
STATS = {}
_lock = threading.Lock()


class HTTPFail(Exception):
    def __init__(self, msg, status=None, body=None, retry_after=None):
        super().__init__(msg)
        self.status = status
        self.body = body
        self.retry_after = retry_after


def _count(host, key):
    with _lock:
        s = STATS.setdefault(host, {"calls": 0, "ok": 0, "http_err": 0, "net_err": 0, "429": 0})
        s[key] = s.get(key, 0) + 1


def _url(base, path, params):
    u = base + path
    if params:
        u += "?" + urllib.parse.urlencode(params, doseq=True)
    return u


def get_json(base, path, params=None, headers=None, timeout=25, retries=2, backoff=2.0):
    """GET and decode JSON. Retries network errors, 5xx and 429 (honoring retry hints). retries=0 for budgeted
    APIs, where every attempt may count."""
    host = urllib.parse.urlparse(base).netloc
    url = _url(base, path, params)
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    hdr.update(headers or {})
    last = None
    for attempt in range(retries + 1):
        _count(host, "calls")
        try:
            req = urllib.request.Request(url, headers=hdr)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
            _count(host, "ok")
            return json.loads(body.decode("utf-8")) if body else None
        except urllib.error.HTTPError as e:
            body = e.read()[:2000].decode("utf-8", "replace")
            ra = None
            try:
                ra = float(e.headers.get("Retry-After")) if e.headers.get("Retry-After") else None
            except (TypeError, ValueError):
                ra = None
            try:
                j = json.loads(body)
                if isinstance(j, dict) and isinstance(j.get("retry_after_ms"), (int, float)):
                    ra = j["retry_after_ms"] / 1000.0
            except ValueError:
                pass
            if e.code == 429:
                _count(host, "429")
            else:
                _count(host, "http_err")
            last = HTTPFail(f"HTTP {e.code} for {path}", e.code, body, ra)
            if e.code == 429 or e.code >= 500:
                if attempt < retries:
                    time.sleep(min(ra or backoff * (attempt + 1), 30))
                    continue
            raise last
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            _count(host, "net_err")
            last = HTTPFail(f"network error for {path}: {e}")
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            raise last
    raise last
