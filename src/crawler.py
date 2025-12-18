import time
import requests
from collections import deque
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

from .utils import normalize_url, same_domain, safe_url

CONTACT_HINTS = (
    "contact", "contacts", "contactez", "nous-contacter",
    "mentions", "mentions-legales", "legal", "rgpd", "privacy", "politique"
)

def _get_config():
    # mini config “sans dépendance yaml”
    # (tu peux remplacer par PyYAML si tu veux)
    return {
        "max_pages": 35,
        "timeout_sec": 15,
        "delay_sec": 1.2,
        "user_agent": "SpectraMediaLeadCollector/1.0 (+contact: vinylesstorefrance@gmail.com)",
        "max_queue": 1500,
        "max_depth": 3,
        "respect_robots": True,
    }

def _fetch(url: str, session: requests.Session, timeout: int) -> str:
    r = session.get(url, timeout=timeout, allow_redirects=True)
    if r.status_code >= 400:
        return ""
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "text/html" not in ctype and "application/xhtml" not in ctype:
        return ""
    r.encoding = r.encoding or "utf-8"
    return r.text or ""

def _score_link(u: str) -> int:
    ul = u.lower()
    score = 0
    for h in CONTACT_HINTS:
        if h in ul:
            score += 10
    return score

def _extract_links(base_url: str, html: str):
    soup = BeautifulSoup(html, "lxml")
    out = []
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        absu = normalize_url(urljoin(base_url, href))
        out.append(absu)
    return out

def crawl_site(start_url: str):
    cfg = _get_config()
    start_url = normalize_url(safe_url(start_url))
    root = start_url

    session = requests.Session()
    session.headers.update({"User-Agent": cfg["user_agent"]})

    seen = set()
    pages = {}

    q = deque()
    q.append((start_url, 0))

    # petite astuce : tenter direct des URLs “contact” courantes
    parsed = urlparse(start_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    for p in ("/contact", "/contact/", "/nous-contacter", "/mentions-legales", "/mentions-legales/"):
        q.appendleft((normalize_url(base + p), 1))

    while q and len(pages) < cfg["max_pages"]:
        if len(q) > cfg["max_queue"]:
            # on coupe si la queue explose
            q = deque(list(q)[: cfg["max_queue"]])

        url, depth = q.popleft()
        if url in seen:
            continue
        seen.add(url)

        if not same_domain(root, url):
            continue
        if depth > cfg["max_depth"]:
            continue

        html = _fetch(url, session, cfg["timeout_sec"])
        time.sleep(cfg["delay_sec"])

        if not html:
            continue

        pages[url] = html

        links = _extract_links(url, html)
        # tri : pages “contact/mentions” d’abord
        links = sorted(set(links), key=_score_link, reverse=True)

        for link in links:
            if link not in seen:
                q.append((link, depth + 1))

    return pages
