import csv
from pathlib import Path

FIELDS = ["name", "city", "website", "phones", "emails", "status"]

def write_output_csv(path: Path, rows: list):
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
