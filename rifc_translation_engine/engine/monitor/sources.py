"""News sources: official feeds and pages first, optional extras second. Every source reports ok, failed,
blocked or skipped with a reason, so coverage gaps are visible instead of silent.

Add your own club feeds in data/monitor_sources.json (see README_MONITOR.md); nothing needs code.
"""
from __future__ import annotations

import email.utils
import html as _html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit

from .. import config
from .http import Http, Robots

SOURCES_FILE = "monitor_sources.json"


@dataclass
class Source:
    id: str
    name: str
    kind: str                       # rss | html_links | gnews | single
    url: str
    league_hint: str | None = None  # league the source covers (context for matching, never a guarantee)
    club_hint: str | None = None
    enabled: bool = True
    link_include: str | None = None  # html_links: regex a link path must match
    link_exclude: str | None = None
    discover_from: str | None = None  # rss: page whose <link rel=alternate type=rss> gives the feed address
    verified: str = ""              # what was actually tested, and when
    note: str = ""
    headline_only: bool = False     # the article body cannot be read (for example redirect links)


MLS = "MLS"
DEFAULT_SOURCES: list[Source] = [
    Source("usl_championship", "USL Championship news (official RSS)", "rss",
           "https://www.uslchampionship.com/news_rss_feed?tags=", "USL Championship",
           discover_from="https://www.uslchampionship.com/news",
           verified="Live check 20 Nov 2026: feed and article pages readable, robots.txt allows."),
    Source("usl_league_one", "USL League One news (official RSS)", "rss",
           "https://www.uslleagueone.com/news_rss_feed?tags=3067503", "USL League One",
           verified="Live check 20 Nov 2026: feed and article pages readable, robots.txt allows."),
    Source("mls_news", "MLSsoccer.com news (page list)", "html_links", "https://www.mlssoccer.com/news/", MLS,
           link_include=r"^/(?:playoffs/\d+/)?news/[a-z0-9][^/]{12,}/?$", link_exclude=r"/news/(topics|index|tags)",
           verified="Live check 20 Nov 2026: page and article links readable, robots.txt allows.",
           note="MLS (first team) news. Useful for players dropping to MLS NEXT Pro or the USL; MLS NEXT Pro has its own page below."),
    Source("mls_next_pro", "MLS NEXT Pro news (page list)", "html_links", "https://www.mlsnextpro.com/news/",
           "MLS Next Pro", link_include=r"^/(?:news|article|stories)/[a-z0-9][^/]{12,}/?$",
           link_exclude=r"/(topics|index|tags)",
           verified="NOT verified: this host was not reachable from the build environment. If it fails here, it is "
                    "shown as failed on the Monitor Status page; add the right feed address in monitor_sources.json."),
    Source("tds_college_men", "TopDrawerSoccer men's college soccer (page list)", "html_links",
           "https://www.topdrawersoccer.com/search/articles/college/men/cid_21", "College",
           link_include=r"^/college-soccer-articles/[a-z0-9]", link_exclude=r"leaderboard|rankings|preview|boxscore",
           verified="Live check 20 Nov 2026: page and article links readable, robots.txt allows.",
           note="College news. No college player data is in this database, so college names stay news leads."),
]


def google_news_sources(cfg: dict) -> list[Source]:
    """Optional. Google News RSS gives wide headline coverage, but news.google.com/robots.txt disallows automated
    clients for /rss/ and its feed notice limits use to personal feed readers. It is therefore off until you turn it
    on in monitor_config.json (google_news.enabled = true). Article text is not readable through its redirect links."""
    if not cfg.get("google_news", {}).get("enabled"):
        return []
    terms = ('(roster OR "option declined" OR "options declined" OR released OR departs OR "not returning" '
             'OR signs OR re-signs OR extension OR loan OR "out of contract")')
    qs = [("gn_uslc", "Google News: USL Championship", '"USL Championship" ' + terms, "USL Championship"),
          ("gn_usl1", "Google News: USL League One", '"USL League One" ' + terms, "USL League One"),
          ("gn_mlsnp", "Google News: MLS NEXT Pro", '"MLS NEXT Pro" ' + terms, "MLS Next Pro"),
          ("gn_rifc", "Google News: Rhode Island FC", '"Rhode Island FC" ' + terms, "USL Championship"),
          ("gn_college", "Google News: college men's soccer", '"men\'s soccer" (college OR NCAA) ("professional contract" '
           'OR "signs" OR "transfer portal" OR "Generation adidas")', "College")]
    days = int(cfg.get("max_age_days", 45))
    return [Source(i, n, "gnews", f"https://news.google.com/rss/search?q={quote_plus(q + f' when:{days}d')}"
                   "&hl=en-US&gl=US&ceid=US:en", lg, headline_only=True,
                   verified="Format checked 20 Nov 2026. Off by default (robots.txt).",
                   note="Headline level only: the article text is not read.") for i, n, q, lg in qs]


def load_sources(cfg: dict | None = None) -> list[Source]:
    cfg = cfg or {}
    srcs = {s.id: s for s in DEFAULT_SOURCES}
    for s in google_news_sources(cfg):
        srcs[s.id] = s
    p = config.DATA_DIR / SOURCES_FILE
    try:
        user = json.loads(p.read_text())
    except Exception:
        user = {}
    for sid in user.get("disable", []):
        if sid in srcs:
            srcs[sid].enabled = False
    for d in user.get("sources", []):
        try:
            srcs[d["id"]] = Source(**{k: v for k, v in d.items() if k in Source.__dataclass_fields__})
        except Exception:
            continue
    return list(srcs.values())


# ---------------------------------------------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------------------------------------------
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
DROP_PARAMS = {"referral", "referrer_id", "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
               "fbclid", "gclid", "oc"}


def clean_text(s: str) -> str:
    return WS.sub(" ", _html.unescape(TAG.sub(" ", s or ""))).strip()


def url_key(url: str) -> str:
    """Stable address for dedupe: no tracking parameters, fragment or trailing slash; lower case host."""
    p = urlsplit(url.strip())
    q = [(k, v) for k, v in parse_qsl(p.query) if k.lower() not in DROP_PARAMS]
    path = p.path.rstrip("/") or "/"
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), path, urlencode(q), ""))


def parse_date(s) -> datetime | None:
    if not s:
        return None
    s = str(s).strip()
    try:
        d = email.utils.parsedate_to_datetime(s)
    except Exception:
        d = None
    if d is None:
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d


def parse_feed(xml_text: str) -> list[dict]:
    """RSS 2.0 or Atom. Returns title, link, published, snippet. Raises ValueError when it is not a feed."""
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"not a valid feed ({exc})")
    out = []
    items = list(root.iter("item")) or [e for e in root.iter() if e.tag.endswith("}entry") or e.tag == "entry"]
    if not items and not any(t.tag in ("channel", "feed") or t.tag.endswith("}feed") for t in root.iter()):
        raise ValueError("not a feed (no channel or feed element)")
    for it in items:
        def g(*names):
            for n in names:
                for c in it:
                    if c.tag == n or c.tag.endswith("}" + n):
                        return c
            return None
        t, l = g("title"), g("link")
        link = (l.get("href") if l is not None and l.get("href") else (l.text if l is not None else "")) or ""
        d = g("pubDate", "published", "updated", "date")
        desc = g("description", "summary", "content")
        title = clean_text(t.text if t is not None else "")
        if not title or not link.strip():
            continue
        out.append({"title": title, "url": link.strip(), "published": parse_date(d.text if d is not None else None),
                    "snippet": clean_text(desc.text if desc is not None else "")[:600]})
    return out


def parse_link_list(page: str, base_url: str, include: str | None, exclude: str | None) -> list[dict]:
    inc, exc = re.compile(include) if include else None, re.compile(exclude) if exclude else None
    out, seen = [], set()
    for href, inner in re.findall(r'(?is)<a\b[^>]*?href="([^"#]+)"[^>]*>(.*?)</a>', page):
        full = urljoin(base_url, _html.unescape(href))
        path = urlsplit(full).path
        if urlsplit(full).netloc != urlsplit(base_url).netloc:
            continue
        if inc and not inc.search(path):
            continue
        if exc and exc.search(path):
            continue
        text = clean_text(inner)
        parts = re.split(r"\s{2,}", _html.unescape(TAG.sub("  ", inner)).strip())
        parts = [clean_text(x) for x in parts if clean_text(x)]
        if len(parts) > 1 and len(parts[0]) < 28 and len(" ".join(parts[1:])) > 20:
            text = " ".join(parts[1:])                  # drop a leading label such as "Transfer Tracker"
        k = url_key(full)
        if len(text) < 20 or k in seen:
            continue
        seen.add(k)
        out.append({"title": text, "url": full, "published": None, "snippet": ""})
    return out


STOP_AT = ("follow the", "most recent news", "most read", "related ", "read more", "share this", "latest news",
           "more from", "you may also like", "advertisement", "sign up for", "subscribe", "view all", "photo courtesy",
           "all news")


def article_text(page: str) -> tuple[str, str, datetime | None]:
    """(title, main text, published). Takes paragraphs and list items from the article, stops at sidebars."""
    title = ""
    m = re.search(r'(?is)<meta[^>]+property="og:title"[^>]+content="([^"]*)"', page)
    if m:
        title = clean_text(m.group(1))
    elif (t := re.search(r"(?is)<title>(.*?)</title>", page)):
        title = clean_text(t.group(1))
    pub = None
    for pat in (r'(?is)<meta[^>]+property="article:published_time"[^>]+content="([^"]+)"',
                r'(?is)"datePublished"\s*:\s*"([^"]+)"', r'(?is)<time[^>]+datetime="([^"]+)"'):
        m = re.search(pat, page)
        if m:
            pub = parse_date(m.group(1))
            if pub:
                break
    body = re.sub(r"(?is)<(script|style|nav|header|footer|aside|form|noscript)\b[^>]*>.*?</\1>", " ", page)
    paras = []
    for inner in re.findall(r"(?is)<(?:p|li|h2|h3|h4|blockquote)\b[^>]*>(.*?)</(?:p|li|h2|h3|h4|blockquote)>", body):
        t = clean_text(inner)
        if not t or t.startswith("-->"):
            continue
        low = t.lower()
        if any(low.startswith(s) for s in STOP_AT) and paras:
            break
        if len(t) >= 18:
            paras.append(t)
    return title, "\n".join(paras)[:24000], pub


# ---------------------------------------------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------------------------------------------
@dataclass
class SourceResult:
    source: Source
    status: str                      # ok | failed | blocked | skipped
    error: str | None = None
    items: list = field(default_factory=list)
    note: str = ""

    def public(self) -> dict:
        return {"id": self.source.id, "name": self.source.name, "status": self.status, "error": self.error,
                "items": len(self.items), "note": self.note}


def fetch_source(src: Source, http: Http, robots: Robots) -> SourceResult:
    if not src.enabled:
        return SourceResult(src, "skipped", note="Turned off")
    url = src.url
    if src.kind == "single":                      # one article address you pasted in
        st, title, text, pub = fetch_article(url, http, robots)
        if not st.startswith("ok"):
            return SourceResult(src, "blocked" if st.startswith("blocked") else "failed", st)
        it = {"title": title or url, "url": url, "published": pub, "snippet": text[:300], "source_id": src.id,
              "league_hint": src.league_hint, "club_hint": src.club_hint, "headline_only": False}
        return SourceResult(src, "ok", items=[it])
    if src.kind != "gnews":                       # Google News is only reachable when you opted in
        ok, why = robots.allowed(url)
        if not ok:
            return SourceResult(src, "blocked", why)
    if src.kind == "rss" and src.discover_from:
        page = http.get(src.discover_from)
        if page.ok:
            ok, why = robots.allowed(src.discover_from)
            if not ok:
                return SourceResult(src, "blocked", why)
            m = re.search(r'<link[^>]+type="application/rss\+xml"[^>]+href="(/news_rss_feed\?tags=[0-9,]+)"', page.text) \
                or re.search(r'<link[^>]+href="([^"]*\?tags=[0-9,]+)"[^>]+type="application/rss\+xml"', page.text)
            if m:
                url = urljoin(src.discover_from, _html.unescape(m.group(1)))
        if url == src.url:
            return SourceResult(src, "failed", "Could not find the feed address on the news page "
                                               "(the site may have changed). Add the feed in monitor_sources.json.")
    r = http.get(url)
    if r.status in (401, 403):
        return SourceResult(src, "blocked", f"The site refused access (HTTP {r.status}).")
    if not r.ok:
        return SourceResult(src, "failed", f"{r.error}")
    try:
        if src.kind in ("rss", "gnews"):
            items = parse_feed(r.text)
        else:
            items = parse_link_list(r.text, url, src.link_include, src.link_exclude)
            if not items:
                return SourceResult(src, "failed", "The page loaded but no article links were found "
                                                   "(the page layout may have changed).")
    except ValueError as exc:
        return SourceResult(src, "failed", str(exc))
    for it in items:
        it["source_id"] = src.id
        it["league_hint"] = src.league_hint
        it["club_hint"] = src.club_hint
        it["headline_only"] = src.headline_only
    return SourceResult(src, "ok", items=items, note=src.note)


def fetch_article(url: str, http: Http, robots: Robots) -> tuple[str, str, str, datetime | None]:
    """(status, title, text, published). status: ok | blocked | failed."""
    ok, why = robots.allowed(url)
    if not ok:
        return f"blocked: {why}", "", "", None
    r = http.get(url, max_bytes=1_500_000)
    if r.status in (401, 403):
        return f"blocked: HTTP {r.status}", "", "", None
    if not r.ok:
        return f"failed: {r.error}", "", "", None
    t, txt, pub = article_text(r.text)
    if len(txt) < 30:
        return "failed: no readable article text", t, "", pub
    return "ok", t, txt, pub
