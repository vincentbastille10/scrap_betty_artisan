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


def _ollama_pick_model(requested: str) -> str:
    """Trouve un modèle Ollama réellement installé (le demandé, sinon un gemma/llama)."""
    try:
        tags = requests.get("http://localhost:11434/api/tags", timeout=10).json()
        names = [m["name"] for m in tags.get("models", [])]
    except Exception as e:
        print(f"⚠️  Ollama injoignable sur localhost:11434 ({e}). Lance-le, ou utilise --cities.")
        return ""
    if not names:
        print("⚠️  Aucun modèle Ollama installé (ollama pull gemma3). Utilise --cities en attendant.")
        return ""
    # match exact, sinon préfixe (gemma3 → gemma3:latest), sinon 1er gemma/llama, sinon 1er
    for n in names:
        if n == requested or n.split(":")[0] == requested:
            return n
    for kw in ("gemma", "llama", "mistral", "qwen"):
        for n in names:
            if kw in n:
                print(f"ℹ️  Modèle '{requested}' absent → j'utilise '{n}'.")
                return n
    print(f"ℹ️  Modèle '{requested}' absent → j'utilise '{names[0]}'.")
    return names[0]


def ollama_cities(n: int, model: str) -> list[str]:
    """Demande N villes US à Ollama (local, gratuit)."""
    m = _ollama_pick_model(model)
    if not m:
        return []
    prompt = (f"List exactly {n} different mid-size US cities good for real estate "
              f"prospecting. Output ONLY the city names, one per line, no state, no "
              f"numbering, no extra text.")
    try:
        r = requests.post("http://localhost:11434/api/generate",
                          json={"model": m, "prompt": prompt, "stream": False}, timeout=180)
        j = r.json()
        txt = j.get("response", "") or ""
        if not txt and j.get("error"):
            print(f"⚠️  Ollama a répondu: {j['error']}")
        cities = [re.sub(r"^[\d.\-)\*\s]+", "", l).strip(" .") for l in txt.splitlines() if l.strip()]
        cities = [c for c in cities if 2 < len(c) < 30 and not c.lower().startswith(("here", "sure", "these"))]
        if not cities:
            print("⚠️  Ollama n'a pas renvoyé de villes exploitables — utilise --cities.")
        return cities[:n]
    except Exception as e:
        print(f"⚠️  Ollama indisponible ({e}) — donne des villes avec --cities.")
        return []


def _ddg(query: str) -> str:
    """1 requête DDG HTML, avec retry après pause si throttlé (HTTP 202/vide)."""
    for attempt in range(2):
        try:
            r = requests.post("https://html.duckduckgo.com/html/",
                              data={"q": query}, headers={"User-Agent": UA}, timeout=20)
            if r.status_code == 200 and "result__a" in r.text:
                return r.text
        except Exception:
            pass
        if attempt == 0:
            print("     …DDG throttlé, pause 20 s…")
            time.sleep(20)
    return ""


def discover(city: str, n: int, niche: str = "real estate brokerage") -> list[str]:
    """Scrape DuckDuckGo HTML (IP résidentielle locale) → URLs racine d'entreprises.
    1 seule requête par ville pour ménager le rate-limit."""
    from urllib.parse import unquote
    out, seen = [], set()
    html = _ddg(f"{niche} {city}")
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
            break
    return out


def main():
    ap = argparse.ArgumentParser(description="Auto-machine realtors US (local)")
    ap.add_argument("--cities", nargs="*", default=[])
    ap.add_argument("--ollama", type=int, default=0, help="génère N villes via Ollama")
    ap.add_argument("--model", default="gemma3")
    ap.add_argument("--per-city", type=int, default=8)
    ap.add_argument("--delay", type=int, default=8, help="pause (s) entre villes pour éviter le rate-limit DDG")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--metier", default="realtor", help="métier cible (doit exister dans lib/metiers.js)")
    ap.add_argument("--niche", default="real estate brokerage", help="terme de recherche (ex: 'esthetician spa', 'dental clinic')")
    ap.add_argument("--lang", default="", choices=["", "fr", "en"], help="langue explicite du site+email (déduite de la région ciblée) ; vide = défaut du métier")
    ap.add_argument("--resend-days", type=int, default=3, help="ne pas re-contacter un email avant N jours")
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
    for idx, c in enumerate(cities):
        found = discover(c, args.per_city, args.niche)
        print(f"  🔎 {c}: {len(found)} courtiers")
        for f in found:
            if f not in urls:
                urls.append(f)
        if idx < len(cities) - 1:
            time.sleep(args.delay)
    urls = urls[: args.limit]
    print(f"\n→ {len(urls)} courtiers à traiter\n")

    # 2) Scrape + 3) génération
    from datetime import datetime
    SENT = Path(__file__).resolve().parent.parent / "outputs" / "sent_log.csv"
    SENT.parent.mkdir(exist_ok=True)
    sent = {}
    if SENT.exists():
        for ln in SENT.read_text().splitlines():
            pr = ln.split(",", 1)
            if len(pr) == 2:
                sent[pr[0]] = pr[1]

    def recently(email):
        try:
            return email in sent and (datetime.now() - datetime.fromisoformat(sent[email])).days < args.resend_days
        except Exception:
            return False

    created = 0
    for i, u in enumerate(urls, 1):
        row = process_site(u, obscura_mode=("auto" if obs else "off"), timeout=args.timeout, metier_override=args.metier)
        miss = [k for k in ("email", "name", "city") if not row.get(k)]
        if miss:
            print(f"[{i}/{len(urls)}] ⏭️  {u} — manque {','.join(miss)}")
            continue
        if args.go and recently(row["email"]):
            print(f"[{i}/{len(urls)}] ⏭️  {row['email']} déjà contacté (< {args.resend_days}j)")
            continue
        if not args.go:
            print(f"[{i}/{len(urls)}] • {row['name']} | {row['email']} | {row['city']}")
            continue
        try:
            r = requests.post(args.site.rstrip('/') + "/api/generate-site",
                              json={"metier": args.metier, "nom_enseigne": row["name"], "ville": row["city"],
                                    "email": row["email"], "plan": "site+betty", "betty_on": True,
                                    "lang": args.lang or None}, timeout=60)
            d = r.json()
            if r.ok:
                created += 1
                with open(SENT, "a") as sf:
                    sf.write(f"{row['email']},{datetime.now().isoformat()}\n")
                print(f"[{i}/{len(urls)}] ✅ {row['name']} → {d.get('url')}")
            else:
                print(f"[{i}/{len(urls)}] ❌ {row['name']} — {d.get('error', r.status_code)}")
        except Exception as e:
            print(f"[{i}/{len(urls)}] ❌ {u} — {e}")
        time.sleep(0.3)

    print(f"\n{'✅ '+str(created)+' sites créés + emails envoyés.' if args.go else 'ℹ️  Aperçu — relance avec --go pour créer + envoyer.'}")


if __name__ == "__main__":
    main()
