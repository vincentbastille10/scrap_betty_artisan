import csv
import os
from pathlib import Path

from src.crawler import crawl_site
from src.extract import extract_contacts_from_html_pages
from src.normalize import normalize_phone_fr
from src.storage import write_output_csv
from src.utils import safe_url

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
INPUT_CSV = DATA_DIR / "input_artisans.csv"
OUTPUT_CSV = DATA_DIR / "output_leads.csv"

def read_input_rows(path: Path):
    rows = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append({k: (v or "").strip() for k, v in r.items()})
    return rows

def main():
    DATA_DIR.mkdir(exist_ok=True)

    if not INPUT_CSV.exists():
        raise SystemExit(f"Fichier manquant: {INPUT_CSV}")

    rows = read_input_rows(INPUT_CSV)
    results = []

    for i, r in enumerate(rows, start=1):
        name = r.get("name", "")
        city = r.get("city", "")
        website = safe_url(r.get("website", ""))

        if not website:
            # Sans API de recherche, on ne devine pas le site automatiquement.
            results.append({
                "name": name, "city": city, "website": "",
                "phones": "", "emails": "",
                "status": "NO_WEBSITE"
            })
            continue

        print(f"[{i}/{len(rows)}] Crawl: {website}")

        pages = crawl_site(website)  # dict[url] = html
        phones, emails = extract_contacts_from_html_pages(pages)

        # normalisation FR des téléphones
        norm_phones = []
        for p in phones:
            np = normalize_phone_fr(p)
            if np and np not in norm_phones:
                norm_phones.append(np)

        results.append({
            "name": name,
            "city": city,
            "website": website,
            "phones": " | ".join(norm_phones),
            "emails": " | ".join(emails),
            "status": "OK" if (norm_phones or emails) else "NO_CONTACT_FOUND"
        })

    write_output_csv(OUTPUT_CSV, results)
    print(f"\n✅ Terminé. Export: {OUTPUT_CSV}")

if __name__ == "__main__":
    main()
