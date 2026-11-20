"""Small HTML helpers using only the standard library (no new packages in the app)."""
from __future__ import annotations

import html as _html
import re
from datetime import datetime
from urllib.parse import urljoin

TAG = re.compile(r"<[^>]+>")


def clean(s: str) -> str:
    return re.sub(r"\s+", " ", _html.unescape(TAG.sub(" ", s or ""))).strip()


def parse_ngin_date(s: str):
    """'11/10/2026, 4:30pm EST' -> datetime (local wall time as published; the zone letters are dropped)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4}),?\s*(\d{1,2}):(\d{2})\s*([ap]m)", s or "", re.I)
    if not m:
        m2 = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", s or "")
        return datetime(int(m2.group(3)), int(m2.group(1)), int(m2.group(2))) if m2 else None
    mo, d, y, hh, mi, ap = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4)), int(m.group(5)), m.group(6).lower()
    hh = hh % 12 + (12 if ap == "pm" else 0)
    return datetime(y, mo, d, hh, mi)


def ngin_items(page: str, base_url: str = "") -> list[dict]:
    """News list blocks used by the USL league sites (and many Sportngin club sites): title link, date, teaser."""
    out = []
    for m in re.finditer(r'<div class="(?:odd|even)[^"]*item[^"]*">(.*?)<span class="clearAll"></span>\s*</div>', page, re.S):
        b = m.group(1)
        a = re.search(r'<h4><a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not a:
            continue
        d = re.search(r'class="newsDate">(.*?)</span>', b, re.S)
        t = re.search(r'class="teaser[^"]*">(.*?)</li>', b, re.S)
        url = urljoin(base_url, _html.unescape(a.group(1)).split("?")[0])
        out.append({"title": clean(a.group(2)), "url": url, "published": parse_ngin_date(d.group(1)) if d else None,
                    "snippet": clean(t.group(1)) if t else ""})
    return out


def anchors(page: str, base_url: str = "") -> list[tuple[str, str]]:
    out = []
    for href, inner in re.findall(r'(?is)<a\b[^>]*?href="([^"#]+)"[^>]*>(.*?)</a>', page):
        out.append((urljoin(base_url, _html.unescape(href)), clean(inner)))
    return out


def find_links(page: str, base_url: str, words: tuple, same_host: bool = True) -> list[tuple[str, str]]:
    """Links whose text or address contains one of the words (used to discover a club's news and roster pages)."""
    from urllib.parse import urlsplit
    host = urlsplit(base_url).netloc.replace("www.", "")
    out, seen = [], set()
    for url, text in anchors(page, base_url):
        if same_host and urlsplit(url).netloc.replace("www.", "") != host:
            continue
        hay = (text + " " + urlsplit(url).path).lower()
        if any(w in hay for w in words) and url not in seen:
            seen.add(url)
            out.append((url, text))
    return out


def feed_links(page: str, base_url: str) -> list[str]:
    return [urljoin(base_url, _html.unescape(h)) for h in re.findall(
        r'(?is)<link[^>]+type="application/(?:rss|atom)\+xml"[^>]*href="([^"]+)"', page)] + [
        urljoin(base_url, _html.unescape(h)) for h in re.findall(r'(?is)<link[^>]+href="([^"]+)"[^>]*type="application/(?:rss|atom)\+xml"', page)]


def plain_text(page: str) -> str:
    t = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page)
    t = re.sub(r"(?i)</(p|div|li|tr|h\d|section|ul|ol|table|br)>|<br\s*/?>", "\n", t)
    t = _html.unescape(TAG.sub(" ", t))
    return "\n".join(re.sub(r"[ \t ]+", " ", l).strip() for l in t.splitlines() if l.strip())
