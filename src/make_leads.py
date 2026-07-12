#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_leads.py — Extracteur contact propre → CSV prêt pour lead_factory.

Prend une liste de sites d'artisans et produit un CSV au format attendu par
outreach/lead_factory.py :  site,email,name,metier,city,phone,source

Corrige les deux bugs de l'ancien scrape_simplebo_directory.py :
  1. Le téléphone était extrait sur le HTML BRUT (capturait les chiffres des
     <script>/JSON/timestamps) → ici on lit le texte VISIBLE + les liens tel:.
  2. Aucune validation de la longueur → un blob de 40 chiffres passait. Ici on
     valide chaque numéro via normalize.normalize_phone_fr (rejette tout ce qui
     n'est pas un vrai numéro FR à 9 chiffres).

En bonus : décodage Cloudflare (data-cfemail), JSON-LD, nom d'entreprise
(<title>/og:site_name), métier deviné, et fallback JS via obscura pour les
sites Simplebo qui cachent le contact derrière « Afficher le téléphone ».

USAGE
  # depuis la liste de sites déjà collectée :
  python src/make_leads.py --in simplebo_artisans.csv --out outputs/leads_clean.csv

  # limiter / activer le fallback obscura (rend le JS) :
  python src/make_leads.py --in simplebo_artisans.csv --out outputs/leads_clean.csv --limit 20 --obscura auto

Le CSV de sortie se donne tel quel à l'usine :
  python ~/betty_abonnement_version2/outreach/lead_factory.py --csv outputs/leads_clean.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

SRC_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SRC_DIR))
from normalize import normalize_phone_fr          # noqa: E402  (validation FR stricte)

def _resolve_obscura() -> str:
    """Résout le binaire obscura même sans PATH (cas d'un lancement depuis le Dock)."""
    v = os.getenv("OBSCURA_BIN")
    if v:
        return v
    if shutil.which("obscura"):
        return "obscura"
    p = Path.home() / ".local" / "bin" / "obscura"
    return str(p) if p.exists() else "obscura"


OBSCURA_BIN = _resolve_obscura()

# --- Emails à jeter (plateformes, hébergeurs, trackers, exemples) -----------
BAD_EMAIL_DOMAINS = {
    "simplebo.fr", "sbcdnsb.com", "sentry.io", "wixpress.com", "example.com",
    "example.org", "godaddy.com", "squarespace.com", "wix.com", "domain.com",
    "email.com", "yourdomain.com", "sentry-next.wixpress.com",
    # Plateformes / builders / hébergeurs (l'email n'est pas celui du prospect)
    "moatable.com", "houzeo.com", "agentfire.com", "peerspace.com", "sortlist.com",
    "classpass.com", "booksy.com", "vagaro.com", "fresha.com", "treatwell.com",
    "planity.com", "mindbodyonline.com", "wellnessliving.com", "sentry.wixpress.com",
    "wordpress.com", "shopify.com", "weebly.com", "webflow.com", "duda.co",
    "godaddysites.com", "wixsite.com", "cloudflare.com", "sentry-cdn.com",
    # Multinationales / grandes franchises (pas des petits prospects locaux)
    "colliers.com", "cbre.com", "jll.com", "cushwake.com", "kw.com", "remax.com",
    "century21.com", "coldwellbanker.com", "compass.com", "sothebys.com",
    "sothebysrealty.com", "exprealty.com",
}
BAD_EMAIL_SUBSTR = ("@2x", ".png", ".jpg", ".gif", ".webp", ".svg", "@sentry")

# Parties locales (avant le @) techniques : jamais l'adresse commerciale du prospect.
BAD_EMAIL_LOCALPARTS = {
    "bugreport", "no-reply", "noreply", "donotreply", "do-not-reply", "postmaster",
    "mailer-daemon", "webmaster", "abuse", "privacy", "dpo", "legal", "root",
    "wordpress", "notifications", "notification", "alerts", "alert", "security",
    "unsubscribe", "bounce", "bounces", "mailer", "no_reply", "cron", "system",
}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Téléphone FR strict : +33 X XX XX XX XX  ou  0X XX XX XX XX (9 chiffres après l'indicatif)
PHONE_RE = re.compile(r"(?:\+33|0033|0)\s?\d(?:[\s.\-]?\d){8}")

METIER_KEYWORDS = [
    ("plombier", ["plombier", "plomberie", "sanitaire", "chauffagiste"]),
    ("electricien", ["electric", "électric", "elec ", "domotique"]),
    ("serrurier", ["serrur", "serrurerie"]),
    ("peintre", ["peintre", "peinture", "platrerie", "plâtrerie", "ravalement"]),
    ("menuisier", ["menuis", "menuiserie", "ebenis", "ébénis"]),
    ("macon", ["macon", "maçon", "maconnerie", "maçonnerie", "gros oeuvre"]),
    ("carreleur", ["carrel", "carrelage", "faience"]),
    ("couvreur", ["couvreur", "couverture", "toiture", "zinguerie"]),
    ("chauffagiste", ["chauffage", "pac ", "pompe a chaleur", "clim"]),
    ("paysagiste", ["paysag", "jardin", "espaces verts", "elagage", "élagage"]),
    ("terrassier", ["terrassement", "vrd", "assainissement"]),
    ("artisan", ["renovation", "rénovation", "batiment", "bâtiment", "habitat", "travaux"]),
]


def guess_metier(*texts: str) -> str:
    blob = " ".join(t.lower() for t in texts if t)
    for metier, kws in METIER_KEYWORDS:
        if any(kw in blob for kw in kws):
            return metier
    return "artisan"


US_STATES = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA",
    "ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK",
    "OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
}


_NOT_CITY = {"Home", "Sell", "Buy", "Search", "Contact", "About", "Menu", "Real",
             "Estate", "Team", "Login", "Sign", "View", "Our", "The"}


UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Plateformes / annuaires nationaux : ce ne sont pas des prospects, on les exclut.
DIRECTORY_DOMAINS = {
    "classpass.com", "peerspace.com", "sortlist.com", "yelp.com", "tripadvisor.com",
    "houzz.com", "thumbtack.com", "angi.com", "booksy.com", "vagaro.com", "fresha.com",
    "treatwell.com", "planity.com", "mindbodyonline.com", "wellnessliving.com",
    "facebook.com", "instagram.com", "linkedin.com", "nextdoor.com", "bbb.org",
}

# Pages internes à visiter (max 5), dans l'ordre.
# Home d'abord (couleur/image/nom), puis pages contact/à-propos/mentions légales
# (les « mentions légales » FR et « Impressum » DE contiennent presque toujours
# l'email). Visitées seulement tant qu'aucun email n'est trouvé (break early).
CONTACT_PATHS = ["", "contact", "contact-us", "contactez-nous", "nous-contacter",
                 "about", "about-us", "a-propos", "equipe", "team", "notre-equipe",
                 "mentions-legales", "mentions", "legal", "impressum", "coordonnees"]

# Mots qui ne sont jamais une ville (garde-fou sur le motif « City, ST »).
_CITY_STOP = {
    "Home", "Sell", "Buy", "Search", "Contact", "About", "Menu", "Real", "Estate",
    "Team", "Login", "Sign", "View", "Our", "The", "Visa", "Visas", "Xxl", "Xxxl",
    "Size", "Sizes", "Color", "Colors", "Price", "Prices", "New", "Sale", "Shop",
    "Blog", "News", "Terms", "Privacy", "Careers", "Services", "Service", "Book",
    # mots d'enseigne souvent captés à tort comme ville
    "Broker", "Brokers", "Brokerage", "Realty", "Realtor", "Realtors", "Agency",
    "Group", "Studio", "Studios", "Photography", "Photo", "Contractors", "Company",
    "Properties", "Homes", "Advisors", "Partners", "Associates", "Realestate",
}


def _clean_name(s: str) -> str:
    """Nettoie un titre → nom d'enseigne (coupe les suffixes bruyants)."""
    return re.split(r"\s*[|–—:·]\s*| - ", (s or "").strip())[0].strip()[:120]


def _name_ok(s: str) -> bool:
    """Écarte un slogan/phrase pris pour un nom d'enseigne
    (ex. 'We do what we say we do with integrity, excellence and passion')."""
    if not s:
        return False
    if len(s.split()) > 6:          # un nom d'enseigne dépasse rarement 6 mots
        return False
    if s.lower().startswith(("we ", "our ", "your ", "welcome")):
        return False
    return True


def _domain_name(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "")
    return host.split(".")[0].replace("-", " ").title()


def parse_jsonld(soup: BeautifulSoup):
    """Retourne (name, city) depuis les blocs JSON-LD (Organization/LocalBusiness/PostalAddress)."""
    import json as _json
    name, city = "", ""
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            data = _json.loads(raw)
        except Exception:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if not name and isinstance(x.get("name"), str):
                    name = x["name"].strip()
                addr = x.get("address")
                for a in (addr if isinstance(addr, list) else [addr]):
                    if isinstance(a, dict) and not city and isinstance(a.get("addressLocality"), str):
                        city = a["addressLocality"].strip()
                for v in x.values():
                    if isinstance(v, (dict, list)):
                        stack.append(v)
            elif isinstance(x, list):
                stack.extend(x)
    return name[:120], city[:60]


_STREET = {"avenue", "ave", "street", "st", "road", "rd", "blvd", "boulevard",
           "drive", "dr", "lane", "ln", "way", "suite", "ste", "court", "ct",
           "place", "pl", "highway", "hwy", "parkway", "pkwy", "floor", "unit",
           "apt", "n", "s", "e", "w",
           "expressway", "expy", "freeway", "fwy", "turnpike", "pike", "loop",
           "trail", "circle", "cir", "terrace", "ter", "plaza", "square", "sq"}


def _clean_city(s: str) -> str:
    """Retire un préfixe d'adresse (numéro + rue) → garde la vraie ville.
    'Edgefield Avenue Dallas' → 'Dallas' ; 'San Antonio' → 'San Antonio'."""
    words = s.split()
    # coupe tout jusqu'au dernier mot de type 'rue' (Avenue, Street…)
    for i in range(len(words) - 1, -1, -1):
        if words[i].lower().strip(".") in _STREET:
            words = words[i + 1:]
            break
    words = [w for w in words if not any(ch.isdigit() for ch in w)]
    return " ".join(words).strip()


def city_from_text(text: str) -> str:
    """Ville depuis le texte UNIQUEMENT via le motif « City, ST » (virgule + vrai code état).
    Ne devine JAMAIS depuis un mot arbitraire en majuscules (évite 'XXXL', 'Visas')."""
    for m in re.finditer(r"\b(\d*\s*[A-Z][A-Za-z.'’\-]+(?:\s+[A-Z][A-Za-z.'’\-]+){0,3}),\s*([A-Z]{2})\b", text or ""):
        city = _clean_city(m.group(1).strip())
        st = m.group(2)
        if (st in US_STATES and city and city.split()[0] not in _CITY_STOP
                and len(city.split()) <= 3 and re.fullmatch(r"[A-Za-z.'’\- ]{3,40}", city)):
            return city
    return ""


def decode_cfemail(hex_str: str) -> str:
    try:
        r = int(hex_str[:2], 16)
        return "".join(chr(int(hex_str[i:i+2], 16) ^ r) for i in range(2, len(hex_str), 2))
    except Exception:
        return ""


def clean_email(e: str) -> str:
    e = e.strip().lower().strip(".")
    if "@" not in e:
        return ""
    local, dom = e.split("@", 1)
    # Faux positifs du scan HTML brut : images retina (logo@2x.png), assets, libs.
    if dom.rsplit(".", 1)[-1] in {"png", "jpg", "jpeg", "gif", "webp", "svg", "css",
                                   "js", "ico", "mp4", "pdf", "json", "woff", "woff2", "ttf"}:
        return ""
    if any(d in dom for d in ("sentry.", "wixpress", "wix.com", "schema.org", "w3.org",
                              "googleapis", "gstatic", "cloudflare", "jsdelivr", "example.",
                              "domain.com", "yourdomain", "email.com", "sentry-cdn")):
        return ""
    if dom in BAD_EMAIL_DOMAINS or dom.endswith(".simplebo.fr") or dom.endswith(".wixpress.com"):
        return ""
    if any(s in e for s in BAD_EMAIL_SUBSTR):
        return ""
    if "." not in dom:
        return ""
    # rejette les adresses techniques/plateforme (bugreport@, no-reply@, legal@…)
    if local in BAD_EMAIL_LOCALPARTS or local.startswith(("noreply", "no-reply", "donotreply")):
        return ""
    return e


def extract_business_name(soup: BeautifulSoup, url: str) -> str:
    og = soup.find("meta", attrs={"property": "og:site_name"})
    if og and og.get("content"):
        return og["content"].strip()[:120]
    if soup.title and soup.title.string:
        t = soup.title.string.strip()
        # coupe les suffixes bruyants type " - Accueil | Plombier Le Mans"
        t = re.split(r"[|–—:·-]", t)[0].strip()
        if t:
            return t[:120]
    return re.sub(r"^https?://(www\.)?", "", url).split("/")[0]


_HEX_RE = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\b")
_RGB_RE = re.compile(r"rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})", re.I)


def _norm_hex(h: str) -> str:
    h = h.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.lower()


def _is_neutral(hexc: str) -> bool:
    """Blanc/noir/gris → pas une couleur de marque exploitable."""
    r, g, b = (int(hexc[i:i + 2], 16) for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    return (mx > 235 and mn > 220) or mx < 38 or (mx - mn) < 26


def _color_to_hex(c: str) -> str:
    hm = _HEX_RE.search(c or "")
    if hm:
        return _norm_hex(hm.group(0))
    rm = _RGB_RE.search(c or "")
    if rm:
        return "#%02x%02x%02x" % tuple(min(255, int(x)) for x in rm.groups())
    return ""


def extract_brand_color(soup: BeautifulSoup, html: str) -> tuple[str, str]:
    """Devine la couleur de marque du site du prospect (pour un site « sur mesure »).
    1) <meta theme-color>  2) variables CSS --primary/--brand/…  3) couleur
    non-neutre la plus fréquente dans les styles. Renvoie (hex, source)."""
    # 1) meta theme-color : explicite, signal le plus fiable
    m = soup.find("meta", attrs={"name": "theme-color"})
    if m and m.get("content"):
        c = _color_to_hex(m["content"])
        if c and not _is_neutral(c):
            return c, "theme-color"
    # 2) variables CSS de marque
    for mv in re.finditer(
        r"--(?:primary|brand|accent|main|theme|color-primary|c-primary)[\w-]*\s*:\s*"
        r"(#[0-9a-fA-F]{3,6}|rgba?\([^)]+\))", html, re.I):
        c = _color_to_hex(mv.group(1))
        if c and not _is_neutral(c):
            return c, "css-var"
    # 3) couleur non-neutre la plus fréquente dans <style> + attributs style,
    #    en écartant les couleurs de réseaux sociaux (icônes ≠ marque).
    from collections import Counter
    blob = " ".join(s.get_text() for s in soup.find_all("style"))
    blob += " " + " ".join(el.get("style", "") for el in soup.select("[style]"))
    cnt: Counter = Counter()
    def _keep(c):
        return not _is_neutral(c) and c not in _SOCIAL_COLORS
    for hm in _HEX_RE.finditer(blob):
        c = _norm_hex(hm.group(0))
        if _keep(c):
            cnt[c] += 1
    for rm in _RGB_RE.finditer(blob):
        c = "#%02x%02x%02x" % tuple(min(255, int(x)) for x in rm.groups())
        if _keep(c):
            cnt[c] += 1
    if cnt:
        return cnt.most_common(1)[0][0], "css-frequent"
    return "", ""


# Couleurs de marque des réseaux sociaux : leurs icônes polluent le comptage.
_SOCIAL_COLORS = {
    "#1877f2", "#3b5998", "#4267b2",           # Facebook
    "#1da1f2", "#1d9bf0",                        # Twitter/X
    "#25d366", "#075e54",                        # WhatsApp
    "#e4405f", "#c13584", "#e1306c",             # Instagram
    "#ff0000",                                    # YouTube
    "#0077b5", "#0a66c2",                        # LinkedIn
    "#bd081c", "#e60023",                        # Pinterest
    "#4285f4", "#ea4335", "#34a853", "#fbbc05", # Google
}


def extract_hero_image(soup: BeautifulSoup, page_url: str) -> str:
    """Image représentative du site du prospect (og:image → twitter:image) pour
    servir de fond « sur mesure ». URL absolue ; écarte logos/favicons/icônes."""
    from urllib.parse import urljoin
    for attrs in ({"property": "og:image"}, {"property": "og:image:url"},
                  {"name": "twitter:image"}, {"name": "twitter:image:src"}):
        tag = soup.find("meta", attrs=attrs)
        src = (tag.get("content") if tag else "") or ""
        src = src.strip()
        if not src:
            continue
        low = src.lower()
        if any(bad in low for bad in ("logo", "favicon", "icon", "sprite", ".svg")):
            continue
        full = urljoin(page_url, src)
        if full.startswith(("http://", "https://")):
            return full
    return ""


def parse_page(html: str, url: str) -> dict:
    """Analyse UNE page : emails (mailto/Cloudflare/texte), candidats nom
    (og:site_name, JSON-LD, title, h1), ville structurée (JSON-LD), + footer/texte."""
    soup = BeautifulSoup(html, "lxml")
    emails, phones = [], []

    for a in soup.select("a[href^='mailto:']"):
        e = clean_email(a.get("href", "")[7:].split("?")[0])
        if e:
            emails.append(e)
    for a in soup.select("a[href^='tel:']"):
        p = normalize_phone_fr(a.get("href", "")[4:])
        if p:
            phones.append(p)
    # Cloudflare : classe dédiée OU n'importe quel élément avec data-cfemail
    for el in soup.select("a.__cf_email__, span.__cf_email__, [data-cfemail]"):
        e = clean_email(decode_cfemail(el.get("data-cfemail", "") or ""))
        if e:
            emails.append(e)

    text = soup.get_text(" ", strip=True)
    for m in EMAIL_RE.findall(text):
        e = clean_email(m)
        if e:
            emails.append(e)
    # Emails obfusqués contre les bots : « nom [at] domaine [dot] com », « (at) », « AT/DOT ».
    for lp, dom in re.findall(
        r"([A-Za-z0-9._%+\-]+)\s*(?:\[at\]|\(at\)|\{at\}|\s+at\s+|@)\s*"
        r"([A-Za-z0-9.\-]+(?:\s*(?:\[dot\]|\(dot\)|\{dot\}|\s+dot\s+)\s*[A-Za-z0-9.\-]+)+)",
        text, re.I):
        dom = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\}|\s+dot\s+)\s*", ".", dom, flags=re.I)
        e = clean_email(f"{lp}@{dom}")
        if e:
            emails.append(e)
    # Scan du HTML BRUT (scripts, JSON, data-*, entités &#64;) : l'email y est
    # souvent présent même quand la page visible n'en montre pas (formulaires).
    import html as _htmlmod
    for m in EMAIL_RE.findall(_htmlmod.unescape(html)):
        e = clean_email(m)
        if e:
            emails.append(e)
    for m in PHONE_RE.findall(text):
        p = normalize_phone_fr(m)
        if p:
            phones.append(p)

    og = soup.find("meta", attrs={"property": "og:site_name"})
    og_name = og["content"].strip() if og and og.get("content") else ""
    jl_name, jl_city = parse_jsonld(soup)
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    h1el = soup.find("h1")
    h1 = h1el.get_text(" ", strip=True) if h1el else ""
    footer = soup.find("footer")
    footer_text = footer.get_text(" ", strip=True)[:2000] if footer else ""
    brand_color, brand_color_src = extract_brand_color(soup, html)
    og_image = extract_hero_image(soup, url)

    return {
        "emails": list(dict.fromkeys(emails)),
        "phones": list(dict.fromkeys(phones)),
        "og_name": og_name, "jl_name": jl_name, "jl_city": jl_city,
        "title": title, "h1": h1, "footer": footer_text, "text": text[:6000],
        "brand_color": brand_color, "brand_color_src": brand_color_src,
        "og_image": og_image,
    }


def obscura_html(url: str, timeout_s: int = 15) -> str:
    """Rend le JS via obscura et renvoie le HTML (pour ré-extraire mailto/tel/cfemail)."""
    if shutil.which(OBSCURA_BIN) is None and not Path(OBSCURA_BIN).exists():
        return ""
    try:
        proc = subprocess.run(
            [OBSCURA_BIN, "fetch", url, "--dump", "html",
             "--wait-until", "networkidle0", "--timeout", str(timeout_s), "--quiet"],
            capture_output=True, text=True, timeout=timeout_s + 15,
        )
        return proc.stdout or ""
    except Exception:
        return ""


CONTACT_HINTS = ("contact", "mention", "legal", "rgpd", "cgv", "a-propos", "apropos")


def find_contact_pages(soup: BeautifulSoup, base_url: str, limit: int = 3) -> list[str]:
    """Repère les liens internes contact / mentions légales (souvent seul endroit avec l'email)."""
    from urllib.parse import urljoin
    base_host = urlparse(base_url).netloc
    found, seen = [], set()
    for a in soup.select("a[href]"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
            continue
        full = urljoin(base_url, href)
        if urlparse(full).netloc != base_host:
            continue
        low = (full + " " + a.get_text(" ", strip=True)).lower()
        if any(h in low for h in CONTACT_HINTS) and full not in seen:
            seen.add(full)
            found.append(full)
        if len(found) >= limit:
            break
    return found


def needs_js(html: str, res: dict) -> bool:
    low = html.lower()
    hidden = "afficher le téléphone" in low or "afficher le telephone" in low or "afficher l'email" in low
    return (not res["phones"] and not res["emails"]) or (hidden and not res["phones"])


def _fetch(url: str, timeout: int) -> str:
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": UA})
        return r.text if r.ok else ""
    except Exception:
        return ""


def process_site(url: str, *, obscura_mode: str, timeout: int,
                 metier_override: str = "", searched_city: str = "") -> dict:
    """Enrichit un domaine : visite jusqu'à 5 pages internes, agrège emails/nom/ville
    depuis des sources structurées, ne bloque QUE si aucun email exploitable."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    host = urlparse(url).netloc.replace("www.", "").lower()
    row = {"site": url, "email": "", "name": "", "metier": metier_override or "", "city": "",
           "phone": "", "city_unverified": "", "email_source": "", "name_source": "",
           "city_source": "", "pages_visited": "", "source": "requests",
           "brand_color": "", "brand_color_source": "", "hero_image": "",
           "has_site": "", "site_url": ""}

    # Exclure annuaires / plateformes nationales (pas des prospects).
    if any(host == d or host.endswith("." + d) for d in DIRECTORY_DOMAINS):
        row["source"] = "skip:annuaire"
        return row

    base = f"https://{host}"
    visited = []
    emails, email_src = [], ""
    og_name = jl_name = title = h1 = jl_city = ""
    brand_color = brand_color_src = hero_image = ""
    home_len = 0
    addr_blob = ""

    for path in CONTACT_PATHS:
        page_url = base + ("/" if not path else "/" + path)
        html = _fetch(page_url, timeout)
        if not html:
            continue
        visited.append(path or "/")
        p = parse_page(html, page_url)
        for e in p["emails"]:
            if e not in emails:
                if not emails:
                    email_src = f"page:{path or '/'}"
                emails.append(e)
        if not row["phone"] and p["phones"]:
            row["phone"] = p["phones"][0]
        og_name = og_name or p["og_name"]
        jl_name = jl_name or p["jl_name"]
        jl_city = jl_city or p["jl_city"]
        title = title or p["title"]
        h1 = h1 or p["h1"]
        if not brand_color and p["brand_color"]:  # couleur de la home d'abord
            brand_color, brand_color_src = p["brand_color"], p["brand_color_src"]
        if not hero_image and p["og_image"]:       # image de la home d'abord
            hero_image = p["og_image"]
        if not path:                                 # longueur de contenu de la home
            home_len = len(p["text"])
        addr_blob += " " + p["footer"] + " " + (p["text"] if path else "")
        # Dès qu'on a un email, inutile de visiter les autres pages (on a déjà
        # récupéré couleur/image/nom sur la home) → gain de temps.
        if emails:
            break

    # Fallback obscura (rend le JS) si toujours pas d'email et mode actif.
    if not emails and obscura_mode != "off":
        oh = obscura_html(base + "/", timeout)
        if oh:
            visited.append("obscura:/")
            p = parse_page(oh, base + "/")
            if p["emails"]:
                emails, email_src, row["source"] = p["emails"], "obscura", "obscura"
            og_name = og_name or p["og_name"]
            jl_name = jl_name or p["jl_name"]
            jl_city = jl_city or p["jl_city"]
            title = title or p["title"]
            h1 = h1 or p["h1"]
            if not brand_color and p["brand_color"]:
                brand_color, brand_color_src = p["brand_color"], p["brand_color_src"]
            if not hero_image and p["og_image"]:
                hero_image = p["og_image"]
            addr_blob += " " + p["footer"]

    row["pages_visited"] = ",".join(visited)
    row["brand_color"], row["brand_color_source"] = brand_color, brand_color_src
    row["hero_image"] = hero_image
    # « A un vrai site » = home atteinte avec du contenu réel (≠ page parquée/vide).
    # → détermine l'offre : site absent = offre A (site+Betty), site présent = offre B (Betty seule).
    row["site_url"] = base
    row["has_site"] = "true" if (visited and home_len > 400) else ""

    # NOM : og:site_name → JSON-LD → title → h1 → domaine. On saute une source
    # qui ressemble à un slogan/phrase plutôt que d'en faire un nom d'enseigne.
    row["name"], row["name_source"] = _domain_name(url), "domain"
    for cand, src in ((og_name, "og:site_name"), (jl_name, "json-ld"),
                      (title, "title"), (h1, "h1")):
        cleaned = _clean_name(cand)
        if cleaned and _name_ok(cleaned):
            row["name"], row["name_source"] = cleaned, src
            break

    # VILLE : adresse structurée (JSON-LD) → « City, ST » dans footer/contact →
    # sinon ville recherchée avec city_unverified=true. Jamais un mot arbitraire.
    if jl_city:
        row["city"], row["city_source"] = jl_city, "json-ld"
    else:
        ct = city_from_text(addr_blob) or city_from_text(" ".join([title, h1, og_name]))
        if ct:
            # 'Mo-Pac Austin' : si la ville extraite contient la ville recherchée
            # en sous-mot, on garde la ville recherchée (plus fiable qu'un bout de rue).
            toks = [w.lower() for w in ct.split()]
            if searched_city and searched_city.lower() in toks and ct.lower() != searched_city.lower():
                row["city"], row["city_source"] = searched_city, "searched-match"
            else:
                row["city"], row["city_source"] = ct, "text"
        elif searched_city:
            row["city"], row["city_source"], row["city_unverified"] = searched_city, "searched", "true"

    # EMAIL : seul champ bloquant.
    if emails:
        row["email"], row["email_source"] = emails[0], email_src
    if not row["metier"]:
        row["metier"] = guess_metier(row["name"], title, url)
    return row


def read_sites(path: str) -> list[str]:
    p = Path(path)
    sites: list[str] = []
    if p.suffix.lower() in (".txt",):
        for line in p.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s and not s.startswith("#"):
                sites.append(s)
        return sites
    with open(p, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        col = None
        for c in (reader.fieldnames or []):
            if c.lower() in ("site", "url", "website"):
                col = c
                break
        for r in reader:
            s = (r.get(col) if col else "") or ""
            s = s.strip()
            if s:
                sites.append(s)
    return sites


def main():
    ap = argparse.ArgumentParser(description="Extracteur contact propre → CSV lead_factory")
    ap.add_argument("--in", dest="infile", required=True, help="CSV/TXT de sites (col site/url/website)")
    ap.add_argument("--out", default="outputs/leads_clean.csv", help="CSV de sortie")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--obscura", dest="obscura_mode", choices=["auto", "always", "off"], default="auto",
                    help="fallback JS : auto (si contact caché/absent), always, off")
    ap.add_argument("--metier", default="", help="force le métier (ex: realtor) au lieu de le deviner")
    ap.add_argument("--city", default="", help="ville recherchée (fallback si aucune ville trouvée → city_unverified)")
    args = ap.parse_args()

    sites = read_sites(args.infile)
    if args.limit:
        sites = sites[: args.limit]

    obs_ok = shutil.which(OBSCURA_BIN) is not None or Path(OBSCURA_BIN).exists()
    print(f"\n🧹 make_leads — {len(sites)} site(s) | obscura={'✅' if obs_ok else '❌ (fallback off)'} | mode={args.obscura_mode}\n")

    rows = []
    for i, s in enumerate(sites, 1):
        row = process_site(s, obscura_mode=(args.obscura_mode if obs_ok else "off"), timeout=args.timeout,
                           metier_override=args.metier, searched_city=args.city)
        rows.append(row)
        flag = "📧" if row["email"] else "∅"
        print(f"[{i}/{len(sites)}] {flag} {row['site']}  (pages: {row['pages_visited'] or '-'})")
        cv = " ⚠️non vérifiée" if row["city_unverified"] else ""
        print(f"      name={row['name']!r}[{row['name_source']}]  city={row['city'] or '-'}[{row['city_source']}]{cv}"
              f"  email={row['email'] or '-'}[{row['email_source']}]")

    cols = ["site", "email", "name", "metier", "city", "phone", "city_unverified",
            "email_source", "name_source", "city_source", "pages_visited", "source"]
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    n_email = sum(1 for r in rows if r["email"])
    n_phone = sum(1 for r in rows if r["phone"])
    print(f"\n✅ {len(rows)} sites → {args.out}")
    print(f"   emails: {n_email}  |  téléphones: {n_phone}  |  actionnables email (lead_factory --send): {n_email}")


if __name__ == "__main__":
    main()
