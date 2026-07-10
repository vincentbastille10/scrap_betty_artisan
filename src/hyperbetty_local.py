#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hyperbetty_local.py — L'auto-machine realtors US, EN LOCAL (une commande).

Pourquoi en local : la découverte (scraper les résultats de recherche) et le
passage de Cloudflare (obscura) NE marchent PAS depuis un serveur Vercel (IP
datacenter bloquée). Ta machine a une IP résidentielle → ça marche. En bonus,
Ollama/Gemma (local, gratuit) peut générer la liste de villes.

Chaîne : villes → découverte des courtiers (Bing) → scrape nom/email/ville
(make_leads + obscura) → generate-site hébergé (crée le site + envoie l'email).

USAGE
  # villes explicites, aperçu (ne crée rien) :
  python src/hyperbetty_local.py --cities Houston Miami Dallas

  # laisser Gemma générer 15 villes US, puis créer + envoyer :
  python src/hyperbetty_local.py --ollama 15 --go

  # depuis une liste de villes explicites, envoi réel :
  python src/hyperbetty_local.py --cities Austin "San Antonio" --go \
      --site https://sitea1euro.vercel.app

Options : --per-city 8, --limit 40, --go (sinon aperçu), --site <url generate>,
          --ollama N (génère N villes via Ollama), --model gemma2.
"""
from __future__ import annotations
import argparse, re, sys, time, json
from pathlib import Path
from urllib.parse import urlparse
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_leads import process_site, OBSCURA_BIN  # réutilise l'extraction + obscura

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
PORTALS = ("bing.", "microsoft.", "msn.", "zillow", "realtor.com", "trulia", "redfin",
           "homes.com", "yelp.", "facebook.", "linkedin.", "instagram.", "youtube.",
           "twitter.", "x.com", "wikipedia.", "mapquest.", "indeed.", "glassdoor.",
           "apartments.com", "loopnet.", "google.", "duckduckgo.", "pinterest.",
           "tiktok.", "reddit.", "bbb.org", "yellowpages.", "angi.", "thumbtack.", "nextdoor.",
           "har.com", "realtrends.com", "listwithclever.com", "brokerlistusa.com",
           "homelight.com", "fastexpert.com", "ratemyagent.com", "upnest.com", "niche.com")


def ollama_cities(n: int, model: str) -> list[str]:
    """Demande N villes US à Ollama (local, gratuit)."""
    prompt = (f"List {n} mid-size US cities good for real estate prospecting, "
              f"one per line, city name only, no numbering, no state.")
    try:
        r = requests.post("http://localhost:11434/api/generate",
                          json={"model": model, "prompt": prompt, "stream": False}, timeout=120)
        txt = r.json().get("response", "")
        cities = [re.sub(r"^[\d.\-)\s]+", "", l).strip() for l in txt.splitlines() if l.strip()]
        return [c for c in cities if 2 < len(c) < 30][:n]
    except Exception as e:
        print(f"⚠️  Ollama indisponible ({e}) — donne des villes avec --cities.")
        return []


def discover(city: str, n: int) -> list[str]:
    """Scrape DuckDuckGo HTML (IP résidentielle locale) → URLs racine de courtiers."""
    from urllib.parse import unquote
    out, seen = [], set()
    for q in (f"real estate brokerage {city}", f"independent real estate agent {city} office"):
        try:
            html = requests.post("https://html.duckduckgo.com/html/",
                                 data={"q": q}, headers={"User-Agent": UA}, timeout=20).text
        except Exception:
            continue
        for h in re.findall(r'result__a[^>]+href="([^"]+)"', html):
            m = re.search(r'uddg=([^&]+)', h)
            real = unquote(m.group(1)) if m else h
            try:
                host = urlparse(real).netloc.replace("www.", "").lower()
            except Exception:
                continue
            if "." not in host or any(p in host for p in PORTALS) or host in seen:
                continue
            seen.add(host)
            out.append("https://" + host + "/")
            if len(out) >= n:
                return out
        time.sleep(0.5)
    return out


def main():
    ap = argparse.ArgumentParser(description="Auto-machine realtors US (local)")
    ap.add_argument("--cities", nargs="*", default=[])
    ap.add_argument("--ollama", type=int, default=0, help="génère N villes via Ollama")
    ap.add_argument("--model", default="gemma2")
    ap.add_argument("--per-city", type=int, default=8)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--go", action="store_true", help="crée les sites + envoie (sinon aperçu)")
    ap.add_argument("--site", default="https://sitea1euro.vercel.app", help="base du generate-site")
    ap.add_argument("--timeout", type=int, default=15)
    args = ap.parse_args()

    cities = list(args.cities)
    if args.ollama:
        cities += ollama_cities(args.ollama, args.model)
    if not cities:
        sys.exit("❌ Donne des villes : --cities Houston Miami  (ou --ollama 15)")

    obs = Path(OBSCURA_BIN).exists() or __import__("shutil").which(OBSCURA_BIN)
    print(f"\n🚀 HyperBetty local — villes={len(cities)} | obscura={'✅' if obs else '❌'} | mode={'ENVOI' if args.go else 'APERÇU'}\n")

    # 1) Découverte
    urls = []
    for c in cities:
        found = discover(c, args.per_city)
        print(f"  🔎 {c}: {len(found)} courtiers")
        for f in found:
            if f not in urls:
                urls.append(f)
    urls = urls[: args.limit]
    print(f"\n→ {len(urls)} courtiers à traiter\n")

    # 2) Scrape + 3) génération
    created = 0
    for i, u in enumerate(urls, 1):
        row = process_site(u, obscura_mode=("auto" if obs else "off"), timeout=args.timeout, metier_override="realtor")
        miss = [k for k in ("email", "name", "city") if not row.get(k)]
        if miss:
            print(f"[{i}/{len(urls)}] ⏭️  {u} — manque {','.join(miss)}")
            continue
        if not args.go:
            print(f"[{i}/{len(urls)}] • {row['name']} | {row['email']} | {row['city']}")
            continue
        try:
            r = requests.post(args.site.rstrip('/') + "/api/generate-site",
                              json={"metier": "realtor", "nom_enseigne": row["name"], "ville": row["city"],
                                    "email": row["email"], "plan": "site+betty", "betty_on": True}, timeout=60)
            d = r.json()
            if r.ok:
                created += 1
                print(f"[{i}/{len(urls)}] ✅ {row['name']} → {d.get('url')}")
            else:
                print(f"[{i}/{len(urls)}] ❌ {row['name']} — {d.get('error', r.status_code)}")
        except Exception as e:
            print(f"[{i}/{len(urls)}] ❌ {u} — {e}")
        time.sleep(0.3)

    print(f"\n{'✅ '+str(created)+' sites créés + emails envoyés.' if args.go else 'ℹ️  Aperçu — relance avec --go pour créer + envoyer.'}")


if __name__ == "__main__":
    main()
