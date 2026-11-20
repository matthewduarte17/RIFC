"""Polite HTTP for the monitor: identifies itself, honours robots.txt, retries transient errors with backoff,
caps response size, and reports every failure with a plain reason. Standard library only.

Tests replace `Http.opener` with a fake, so no test touches the network.
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from urllib.parse import quote
from dataclasses import dataclass
from urllib import robotparser
from urllib.parse import urlsplit

UA_TOKEN = "RIFC-recruitment-monitor"
USER_AGENT = f"{UA_TOKEN}/1.0 (+personal scouting use; reads public headlines and short excerpts)"
TRANSIENT = {429, 500, 502, 503, 504}


@dataclass
class Resp:
    status: int = 0
    text: str = ""
    url: str = ""
    error: str | None = None
    content_type: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300


def _safe(url: str) -> str:
    return quote(url, safe=":/?&=%#,;+@~!$'()*[]")


def _default_opener(url: str, timeout: float, max_bytes: int):
    url = _safe(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*",
                                               "Accept-Language": "en-US,en;q=0.8"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read(max_bytes + 1)
            return r.status, dict(r.headers), data[:max_bytes], r.geturl()
    except urllib.error.HTTPError as e:
        try:
            body = e.read(2000)
        except Exception:
            body = b""
        return e.code, dict(e.headers or {}), body, url


class Http:
    def __init__(self, timeout: float = 25, retries: int = 3, sleep=time.sleep, opener=None, host_delay: float = 1.0):
        self.timeout, self.retries, self.sleep = timeout, retries, sleep
        self.opener = opener or _default_opener
        self.host_delay = host_delay
        self._last: dict[str, float] = {}
        self.log: list[str] = []

    def _polite(self, url: str):
        host = urlsplit(url).netloc
        wait = self.host_delay - (time.monotonic() - self._last.get(host, 0))
        if wait > 0:
            self.sleep(wait)
        self._last[host] = time.monotonic()

    def get(self, url: str, max_bytes: int = 2_500_000) -> Resp:
        last = Resp(url=url, error="not attempted")
        for attempt in range(self.retries + 1):
            self._polite(url)
            try:
                status, headers, data, final = self.opener(url, self.timeout, max_bytes)
                ctype = next((v for k, v in headers.items() if k.lower() == "content-type"), "")
                enc = "utf-8"
                if "charset=" in ctype.lower():
                    enc = ctype.lower().split("charset=")[-1].split(";")[0].strip() or "utf-8"
                try:
                    text = data.decode(enc, errors="replace")
                except LookupError:
                    text = data.decode("utf-8", errors="replace")
                last = Resp(status, text, final, None if 200 <= status < 300 else f"HTTP {status}", ctype)
                if status in TRANSIENT and attempt < self.retries:
                    ra = next((v for k, v in headers.items() if k.lower() == "retry-after"), None)
                    delay = min(float(ra), 30) if ra and str(ra).isdigit() else 1.5 * (2.5 ** attempt)
                    self.log.append(f"{url}: HTTP {status}, retry {attempt + 1} in {delay:.0f}s")
                    self.sleep(delay)
                    continue
                return last
            except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, OSError) as exc:
                reason = getattr(exc, "reason", exc)
                last = Resp(0, "", url, f"{type(exc).__name__}: {reason}")
                if "Tunnel connection failed: 40" in str(reason):      # a policy refusal, not a glitch: do not retry
                    return last
                if attempt < self.retries:
                    delay = 1.5 * (2.5 ** attempt)
                    self.log.append(f"{url}: {last.error}, retry {attempt + 1} in {delay:.0f}s")
                    self.sleep(delay)
                    continue
                return last
        return last


class Robots:
    """robots.txt per host, fetched once per run. 404/410: allowed. 401/403 or an unreachable file: not fetched
    (reported as blocked) so a restriction is never guessed around."""

    def __init__(self, http: Http):
        self.http = http
        self.cache: dict[str, tuple[robotparser.RobotFileParser | None, str | None]] = {}

    def allowed(self, url: str) -> tuple[bool, str | None]:
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self.cache:
            r = self.http.get(host + "/robots.txt", max_bytes=300_000)
            if r.status in (404, 410):
                self.cache[host] = (None, None)
            elif r.ok:
                rp = robotparser.RobotFileParser()
                rp.parse(r.text.splitlines())
                self.cache[host] = (rp, None)
            elif r.status in (401, 403):
                self.cache[host] = (None, f"robots.txt refused access (HTTP {r.status}): the site restricts automated readers")
            else:
                self.cache[host] = (None, f"robots.txt could not be read ({r.error}); not fetched this run to stay safe")
        rp, why = self.cache[host]
        if why:
            return False, why
        if rp is None:
            return True, None
        path = parts.path + (("?" + parts.query) if parts.query else "")
        if rp.can_fetch(UA_TOKEN, path or "/") and rp.can_fetch("*", path or "/"):
            return True, None
        return False, f"robots.txt of {parts.netloc} disallows {parts.path or '/'} for automated readers"
