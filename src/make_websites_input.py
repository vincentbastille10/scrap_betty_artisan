import pandas as pd
from pathlib import Path

IN_PATH = Path("data/input_artisans.csv")
OUT_PATH = Path("data/input_websites.csv")

df = pd.read_csv(IN_PATH)

# On garde l'essentiel pour la suite
out = df[["siret", "nom", "commune", "code_postal"]].copy()
out["website"] = ""  # à remplir (manuellement au début, ou via API plus tard)

OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
out.to_csv(OUT_PATH, index=False, encoding="utf-8")
print(f"✅ Généré: {OUT_PATH} ({len(out)} lignes)")
