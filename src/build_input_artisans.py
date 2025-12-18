#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import csv
import time
import requests
from pathlib import Path

API_URL = "https://recherche-entreprises.api.gouv.fr/search"
DEPARTEMENT = "72"
PER_PAGE = 25

KEYWORDS = [
    "plombier", "chauffagiste", "electricien", "menuisier", "serrurier",
    "couvreur", "charpentier", "peintre", "carreleur", "macon",
    "paysagiste", "ramoneur", "vitrier", "isolation", "renovation",
]

OUT_PATH = Path("data/input_artisans.csv")

FIELDS = ["keyword","siren","siret","nom","naf","adresse","code_postal","commune"]

def safe_get(d, *keys, default=""):
    cur = d
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur if cur is not None else default

def fetch_keyword(keyword: str, max_pages: int = 10, sleep_s: float = 0.2):
    results = []
    seen_siret = set()

    for page in range(1, max_pages + 1):
        params = {"q": keyword, "page": page, "per_page": PER_PAGE}
        r = requests.get(API_URL, params=params, timeout=20)
        r.raise_for_status()
        data = r.json()

        items = data.get("results", []) or []
        if not items:
            print(f"  - {keyword}: page {page} -> 0 résultat, stop")
            break

        kept = 0
        for it in items:
            dep = str(safe_get(it, "siege", "departement", default=""))
            if dep != DEPARTEMENT:
                continue

            siret = safe_get(it, "siege", "siret", default="")
            if not siret or siret in seen_siret:
                continue
            seen_siret.add(siret)

            kept += 1
            results.append({
                "keyword": keyword,
                "siren": it.get("siren", "") or "",
                "siret": siret,
                "nom": it.get("nom_complet", "") or it.get("nom_raison_sociale", "") or "",
                "naf": safe_get(it, "siege", "activite_principale", default=""),
                "adresse": safe_get(it, "siege", "adresse", default="") or safe_get(it, "siege", "geo_adresse", default=""),
                "code_postal": safe_get(it, "siege", "code_postal", default=""),
                "commune": safe_get(it, "siege", "libelle_commune", default=""),
            })

        total_pages = int(data.get("total_pages") or 1)
        print(f"  - {keyword}: page {page}/{total_pages} -> gardés {kept}")

        if page >= total_pages:
            break

        time.sleep(sleep_s)

    return results

def main():
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    all_rows = []
    global_seen = set()

    print("Début génération Sarthe (72)…")
    for kw in KEYWORDS:
        print(f"\nMot-clé: {kw}")
        rows = fetch_keyword(kw, max_pages=10)

        new_count = 0
        for row in rows:
            if row["siret"] in global_seen:
                continue
            global_seen.add(row["siret"])
            all_rows.append(row)
            new_count += 1

        print(f"  + ajoutés: {new_count} | total: {len(all_rows)}")

        # écriture à chaque mot-clé (sécurise si interruption)
        tmp = OUT_PATH.with_suffix(".csv.tmp")
        with tmp.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            w.writerows(all_rows)
        tmp.replace(OUT_PATH)

    print(f"\n✅ Terminé: {OUT_PATH} ({len(all_rows)} lignes)")

if __name__ == "__main__":
    main()
