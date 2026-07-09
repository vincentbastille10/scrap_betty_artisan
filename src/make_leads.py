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

OBSCURA_BIN = os.getenv("OBSCURA_BIN", "obscura")

# --- Emails à jeter (plateformes, trackers, exemples) ----------------------
BAD_EMAIL_DOMAINS = {
    "simplebo.fr", "sbcdnsb.com", "sentry.io", "wixpress.com", "example.com",
    "example.org", "godaddy.com", "squarespace.com", "wix.com", "domain.com",
    "email.com", "yourdomain.com", "sentry-next.wixpress.com",
}
BAD_EMAIL_SUBSTR = ("@2x", ".png", ".jpg", ".gif", ".webp", ".svg", "@sentry")

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


def guess_ville(*texts: str) -> str:
    """Devine la ville. US : « City, ST » ou « City ST ». FR/EN : « à/in <Ville> »."""
    blob = " ".join(t for t in texts if t)
    # US avec virgule : "St. Louis, MO" / "Tampa, FL"
    for m in re.finditer(r"\b([A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+){0,2}),\s*([A-Z]{2})\b", blob):
        if m.group(2) in US_STATES and m.group(1).split()[0] not in _NOT_CITY:
            return m.group(1).strip()
    # US sans virgule : "Lutz FL" (un ou deux mots + code état)
    for m in re.finditer(r"\b([A-Z][A-Za-z.\-]+(?:\s+[A-Z][A-Za-z.\-]+)?)\s+([A-Z]{2})\b", blob):
        first = m.group(1).split()[0]
        if m.group(2) in US_STATES and first not in _NOT_CITY:
            return m.group(1).strip()
    # FR/EN : "à Nantes" / "in Austin"
    m = re.search(
        r"\b(?:[àa]|in)\s+([A-ZÀ-Ÿ][\wÀ-ÿ'’\-]+(?:[ \-][A-ZÀ-Ÿ][\wÀ-ÿ'’\-]+){0,2})",
        blob,
    )
    if m:
        ville = re.sub(r"\s*\(?\d{5}\)?$", "", m.group(1).strip()).strip()
        return ville
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
    dom = e.split("@")[-1]
    if dom in BAD_EMAIL_DOMAINS or dom.endswith(".simplebo.fr") or dom.endswith(".wixpress.com"):
        return ""
    if any(s in e for s in BAD_EMAIL_SUBSTR):
        return ""
    if "." not in dom:
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


def extract_from_html(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")

    emails, phones = [], []

    # 1) mailto: / tel: (les plus fiables)
    for a in soup.select("a[href^='mailto:']"):
        e = clean_email(a.get("href", "")[7:].split("?")[0])
        if e:
            emails.append(e)
    for a in soup.select("a[href^='tel:']"):
        p = normalize_phone_fr(a.get("href", "")[4:])
        if p:
            phones.append(p)

    # 2) Cloudflare
    for a in soup.select("a.__cf_email__, span.__cf_email__"):
        e = clean_email(decode_cfemail(a.get("data-cfemail", "") or ""))
        if e:
            emails.append(e)

    # 3) texte VISIBLE seulement (jamais le HTML brut → pas de faux numéros)
    text = soup.get_text(" ", strip=True)
    for m in EMAIL_RE.findall(text):
        e = clean_email(m)
        if e:
            emails.append(e)
    for m in PHONE_RE.findall(text):
        p = normalize_phone_fr(m)
        if p:
            phones.append(p)

    # dédup en gardant l'ordre
    emails = list(dict.fromkeys(emails))
    phones = list(dict.fromkeys(phones))
    name = extract_business_name(soup, url)
    return {"emails": emails, "phones": phones, "name": name,
            "title": (soup.title.string.strip() if soup.title and soup.title.string else ""),
            "text": text[:8000]}


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


def process_site(url: str, *, obscura_mode: str, timeout: int, metier_override: str = "") -> dict:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    row = {"site": url, "email": "", "name": "", "metier": "", "city": "", "phone": "", "source": "requests"}

    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "Mozilla/5.0 (compatible; BettyLeadBot/1.0)"})
        html = r.text
    except Exception as e:
        row["source"] = f"ERR {type(e).__name__}"
        return row

    res = extract_from_html(html, url)

    # Si pas d'email sur l'accueil, on va le chercher sur contact / mentions légales
    # (obligation RGPD : ces pages listent souvent le seul email du site).
    if not res["emails"]:
        try:
            soup0 = BeautifulSoup(html, "lxml")
            for cpage in find_contact_pages(soup0, url):
                try:
                    cr = requests.get(cpage, timeout=timeout,
                                      headers={"User-Agent": "Mozilla/5.0 (compatible; BettyLeadBot/1.0)"})
                    sub = extract_from_html(cr.text, url)
                    res["emails"] = list(dict.fromkeys(res["emails"] + sub["emails"]))
                    res["phones"] = list(dict.fromkeys(res["phones"] + sub["phones"]))
                    if res["emails"]:
                        row["source"] = "requests+contact"
                        break
                except Exception:
                    continue
        except Exception:
            pass

    # Fallback obscura (rend le JS) si demandé et si le contact est manquant/caché
    if obscura_mode != "off" and (obscura_mode == "always" or needs_js(html, res)):
        oh = obscura_html(url, timeout)
        if oh:
            res2 = extract_from_html(oh, url)
            # on garde le meilleur des deux (union)
            res2["emails"] = list(dict.fromkeys(res["emails"] + res2["emails"]))
            res2["phones"] = list(dict.fromkeys(res["phones"] + res2["phones"]))
            res2["name"] = res["name"] or res2["name"]
            if res2["emails"] or res2["phones"]:
                res = res2
                row["source"] = "obscura"

    row["email"]  = res["emails"][0] if res["emails"] else ""
    row["phone"]  = res["phones"][0] if res["phones"] else ""
    row["name"]   = res["name"]
    row["metier"] = metier_override or guess_metier(res["name"], res.get("title", ""), url)
    row["city"]   = guess_ville(res["name"], res.get("title", ""), res.get("text", ""))
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
    args = ap.parse_args()

    sites = read_sites(args.infile)
    if args.limit:
        sites = sites[: args.limit]

    obs_ok = shutil.which(OBSCURA_BIN) is not None or Path(OBSCURA_BIN).exists()
    print(f"\n🧹 make_leads — {len(sites)} site(s) | obscura={'✅' if obs_ok else '❌ (fallback off)'} | mode={args.obscura_mode}\n")

    rows = []
    for i, s in enumerate(sites, 1):
        row = process_site(s, obscura_mode=(args.obscura_mode if obs_ok else "off"), timeout=args.timeout,
                           metier_override=args.metier)
        rows.append(row)
        flag = "📧" if row["email"] else ("📞" if row["phone"] else "∅")
        print(f"[{i}/{len(sites)}] {flag} {row['site']}")
        print(f"      name={row['name']!r}  metier={row['metier']}  email={row['email'] or '-'}  phone={row['phone'] or '-'}  [{row['source']}]")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["site", "email", "name", "metier", "city", "phone", "source"])
        w.writeheader()
        w.writerows(rows)

    n_email = sum(1 for r in rows if r["email"])
    n_phone = sum(1 for r in rows if r["phone"])
    print(f"\n✅ {len(rows)} sites → {args.out}")
    print(f"   emails: {n_email}  |  téléphones: {n_phone}  |  actionnables email (lead_factory --send): {n_email}")


if __name__ == "__main__":
    main()
