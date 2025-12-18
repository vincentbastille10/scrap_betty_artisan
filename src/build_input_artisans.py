#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import time
import requests
from pathlib import Path

API_URL = "https://recherche-entreprises.api.gouv.fr/search"  # API publique
DEPARTEMENT = "72"  # Sarthe
PER_PAGE = 25       # l'API renvoie aussi per_page/page (souvent 10 par défaut)

# Mots-clés "métiers" pour récupérer un max d'artisans
KEYWORDS = [
    "plombier", "chauffagiste", "electricien", "menuisier", "serrurier",
    "couvreur", "charpentier", "peintre", "carreleur", "macon",
    "paysagiste", "ramoneur", "vitrier", "isolation", "renovation",
    "terrassier", "facadier", "electricite", "plomberie",
]

OUT_PATH = Path("data/input_artisans.csv")

def safe_get(d: dict, *keys, default=None):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur

def fetch_keyword(keyword: str, max_pages: int = 60, sleep_s: float = 0.25):
    """
    Récupère des entreprises via /search?q=keyword
    Puis filtre localement sur siege.departement == 72
    """
    results = []
    seen_siret = set()

    for page in range(1, max_pages + 1):
        params = {
            "q": keyword,
            "page": page,
            "per_page": PER_PAGE,
        }
        r = requests.get(API_URL, params=params, timeout=30)
        r.raise_for_status()
        data = r.json()

        items = data.get("results", []) or []
        if not items:
            break

        for it in items:
            dep = str(safe_get(it, "siege", "departement", default="") or "")
            if dep != DEPARTEMENT:
                continue

            siret = safe_get(it, "siege", "siret", default="") or ""
            if not siret or siret in seen_siret:
                continue
            seen_siret.add(siret)

            results.append({
                "keyword": keyword,
                "siren": it.get("siren", ""),
                "siret": siret,
                "nom": it.get("nom_complet", "") or it.get("nom_raison_sociale", "") or "",
                "naf": safe_get(it, "siege", "activite_principale", default="") or "",
                "adresse": safe_get(it, "siege", "adresse", default="") or safe_get(it, "siege", "geo_adresse", default="") or "",
                "code_postal": safe_get(it, "siege", "code_postal", default="") or "",
                "commune": safe_get(it, "siege", "libelle_commune", default="") or "",
            })

        # arrêt si on a déjà parcouru toutes les pages
        total_pages = int(data.get("total_pages") or 1)
        if page >= total_pages:
            break

        time.sleep(sleep_s)

    return results

def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    global_seen = set()

    for kw in KEYWORDS:
        rows = fetch_keyword(kw)
        for row in rows:
            if row["siret"] in global_seen:
                continue
            global_seen.add(row["siret"])
            all_rows.append(row)

    # Écrit le CSV
    fieldnames = ["keyword", "siren", "siret", "nom", "naf", "adresse", "code_postal", "commune"]
    with OUT_PATH.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(all_rows)

    print(f"✅ Généré: {OUT_PATH} ({len(all_rows)} lignes)")

if __name__ == "__main__":
    main()
