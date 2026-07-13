#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard.py — HyperBetty, panneau de contrôle local (Flask, un seul fichier).

Lance un serveur sur http://127.0.0.1:8765 : formulaire (villes/niche/métier),
bouton pour lancer hyperbetty_local.py en tâche de fond, logs en direct,
historique des envois (anti-doublon), et un mode "continu" (relance automatique
toutes les N heures sur un pool de villes tournant).

Lancement : double-clique HyperBetty.app (Dock), ou :
  python3 dashboard.py
"""
from __future__ import annotations
import csv, json, os, random, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, Response

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
LOG_FILE = OUT / "dashboard_log.txt"
SENT_FILE = OUT / "sent_log.csv"
STATE_FILE = OUT / "dashboard_state.json"
PY = sys.executable

CITY_POOL = ["Houston", "Miami", "Dallas", "Austin", "Phoenix", "Denver", "Charlotte",
             "Raleigh", "Nashville", "Memphis", "Atlanta", "Orlando", "Tampa", "San Antonio",
             "Fort Worth", "Columbus", "Indianapolis", "Jacksonville", "Sacramento", "Portland",
             "Seattle", "San Diego", "Las Vegas", "Chicago", "Boston", "Philadelphia",
             "Minneapolis", "Detroit", "Kansas City", "Louisville", "Baltimore", "Milwaukee",
             "Tucson", "Omaha", "Tulsa", "New Orleans", "Cleveland", "Pittsburgh", "Cincinnati",
             "Boise", "Reno", "Richmond", "Des Moines", "Oklahoma City", "Albuquerque",
             "Los Angeles", "New York", "San Jose", "Fresno", "Mesa", "Long Beach", "Oakland",
             "Bakersfield", "Anaheim", "Santa Ana", "Riverside", "Stockton", "Irvine", "Chula Vista",
             "Fremont", "San Bernardino", "Modesto", "Fontana", "Oxnard", "Colorado Springs",
             "Aurora", "Arlington", "Corpus Christi", "Plano", "Laredo", "Lubbock", "Garland",
             "Irving", "Amarillo", "El Paso", "McKinney", "Frisco", "Brownsville", "Killeen",
             "Wichita", "St. Louis", "Springfield", "Lexington", "Buffalo", "Rochester", "Newark",
             "Jersey City", "Chandler", "Scottsdale", "Gilbert", "Glendale", "Tempe", "Henderson",
             "North Las Vegas", "Spokane", "Tacoma", "Vancouver", "Boise", "Salt Lake City",
             "Provo", "Ogden", "Madison", "Green Bay", "Grand Rapids", "Ann Arbor", "Toledo",
             "Akron", "Dayton", "Fort Wayne", "Chattanooga", "Knoxville", "Clarksville", "Birmingham",
             "Montgomery", "Huntsville", "Mobile", "Baton Rouge", "Shreveport", "Little Rock",
             "Jackson", "Columbia", "Charleston", "Greensboro", "Durham", "Winston-Salem",
             "Cary", "Wilmington", "Savannah", "Augusta", "Columbus GA", "Fort Lauderdale",
             "St. Petersburg", "Hialeah", "Cape Coral", "Port St. Lucie", "Tallahassee",
             "Gainesville", "Sarasota", "Naples", "Fort Myers", "Pensacola", "Virginia Beach",
             "Norfolk", "Chesapeake", "Alexandria", "Arlington VA"]

# Pointeurs de rotation pour le mode continu (parcourt villes + métiers sans répéter).
_cont_idx = 0
_met_idx = 0

# Rotation des métiers en continu : (id, niche EN, niche FR). La niche est
# choisie selon la langue de la ville → petites villes FR = recherche française.
METIER_POOL = [
    ("realtor", "real estate brokerage", "agent immobilier"),
    ("estheticienne", "esthetician spa", "institut de beauté"),
    ("coiffeuse", "hair salon", "salon de coiffure"),
    ("coiffeur_barbier", "barber shop", "barbier"),
    ("plombier", "plumbing company", "plombier"),
    ("artisan", "home renovation contractor", "artisan rénovation"),
    ("serrurier", "locksmith", "serrurier"),
    ("garagiste", "auto repair shop", "garage automobile"),
    ("paysagiste", "landscaping company", "paysagiste"),
    ("menage", "house cleaning service", "société de ménage"),
    ("coach", "personal training studio", "coach sportif"),
    ("yoga", "yoga studio", "studio de yoga"),
    ("photographe", "photography studio", "photographe"),
    ("dj", "wedding DJ", "DJ mariage"),
    ("graphiste", "graphic design studio", "graphiste"),
    ("marketing", "marketing agency", "agence marketing"),
    ("osteopathe", "osteopathy clinic", "ostéopathe"),
    ("kine", "physical therapy clinic", "kinésithérapeute"),
    ("dentiste", "dental clinic", "cabinet dentaire"),
    ("veterinaire", "veterinary clinic", "vétérinaire"),
    ("nutritionniste", "nutritionist office", "nutritionniste"),
    ("therapeute", "therapy practice", "thérapeute"),
    ("avocat", "law firm", "avocat"),
    ("comptable", "accounting firm", "expert-comptable"),
    ("assurance", "insurance agency", "courtier en assurance"),
    ("architecte", "architecture firm", "architecte"),
    ("traiteur", "catering company", "traiteur"),
]

# PETITES/MOYENNES VILLES FRANÇAISES = cible prioritaire : petits exploitants
# souvent SANS site → offre A (site + Betty 59€), euros rapides. Email en FR.
CITY_POOL_FR = [
    "Amiens", "Reims", "Le Havre", "Saint-Étienne", "Toulon", "Grenoble", "Dijon",
    "Angers", "Nîmes", "Clermont-Ferrand", "Le Mans", "Brest", "Tours", "Limoges",
    "Metz", "Besançon", "Perpignan", "Orléans", "Rouen", "Mulhouse", "Caen", "Nancy",
    "Avignon", "Poitiers", "Pau", "La Rochelle", "Cannes", "Béziers", "Calais", "Colmar",
    "Bourges", "Ajaccio", "Saint-Nazaire", "Quimper", "Valence", "Troyes", "Montauban",
    "Niort", "Chambéry", "Lorient", "Beauvais", "Cholet", "Vannes", "La Roche-sur-Yon",
    "Laval", "Bayonne", "Belfort", "Angoulême", "Châteauroux", "Tarbes", "Arras", "Blois",
    "Chartres", "Compiègne", "Albi", "Périgueux", "Bourg-en-Bresse", "Agen", "Nevers",
    "Auxerre", "Épinal", "Cahors", "Rodez", "Gap", "Mont-de-Marsan", "Aurillac",
    "Carcassonne", "Narbonne", "Fréjus", "Arles", "Draguignan", "Vichy", "Roanne",
    "Montluçon", "Saint-Malo", "Dax", "Biarritz", "Sète", "Menton", "Annecy",
    "Thonon-les-Bains", "Chalon-sur-Saône", "Mâcon", "Vienne", "Cognac", "Saintes",
    "Rochefort", "Libourne", "Bergerac", "Villefranche-sur-Saône", "Épernay",
    "Charleville-Mézières", "Sedan", "Saint-Brieuc", "Lannion", "Morlaix", "Concarneau",
    "Douarnenez", "Fougères", "Vitré", "Redon", "Dinan", "Pontivy", "Auray", "Landerneau",
    "Guingamp", "Namur", "Charleroi", "Mons", "Tournai", "Liège", "Fribourg", "Neuchâtel", "Sion",
    # Afrique francophone + Maghreb (petits exploitants, email FR)
    "Dakar", "Abidjan", "Douala", "Yaoundé", "Casablanca", "Rabat", "Marrakech", "Tanger",
    "Tunis", "Sfax", "Alger", "Oran", "Bamako", "Ouagadougou", "Cotonou", "Lomé", "Libreville",
    "Brazzaville", "Kinshasa", "Conakry", "Niamey", "Antananarivo", "Kigali", "Pointe-Noire",
]

# PETITES/MOYENNES VILLES US (email + site en anglais).
CITY_POOL_US = [
    "Boise", "Bend", "Missoula", "Bozeman", "Billings", "Fargo", "Sioux Falls", "Rapid City",
    "Cheyenne", "Casper", "Duluth", "Rochester", "Appleton", "Green Bay", "Fort Collins",
    "Boulder", "Provo", "Ogden", "Chico", "Redding", "Eugene", "Salem", "Yakima", "Bellingham",
    "Coeur d'Alene", "Kalispell", "Grand Forks", "Lincoln", "Topeka", "Springfield", "Columbia",
    "Fayetteville", "Chattanooga", "Knoxville", "Asheville", "Greenville", "Savannah", "Augusta",
    "Macon", "Tallahassee", "Gainesville", "Ocala", "Lakeland", "Pensacola", "Mobile",
    "Montgomery", "Huntsville", "Tuscaloosa", "Lafayette", "Shreveport", "Little Rock",
    "Springdale", "Amarillo", "Lubbock", "Midland", "Odessa", "Waco", "Tyler", "Abilene",
    "Santa Fe", "Flagstaff", "Yuma", "Salinas", "Santa Rosa", "Carson City", "Medford",
    "Wichita Falls", "Roswell", "Cedar Rapids", "Peoria", "Rockford", "Fort Smith", "Bismarck",
]

# ROYAUME-UNI (anglais).
CITY_POOL_UK = [
    "Leeds", "Sheffield", "Bradford", "Leicester", "Coventry", "Nottingham", "Newcastle",
    "Sunderland", "Wolverhampton", "Plymouth", "Southampton", "Portsmouth", "Reading", "Derby",
    "Stoke-on-Trent", "Hull", "Preston", "Blackpool", "Middlesbrough", "Bolton", "Norwich",
    "Ipswich", "Oxford", "Cambridge", "York", "Exeter", "Gloucester", "Swindon", "Luton",
    "Milton Keynes", "Northampton", "Peterborough", "Cardiff", "Swansea", "Newport", "Belfast",
    "Aberdeen", "Dundee", "Inverness", "Bournemouth", "Brighton", "Wakefield", "Doncaster",
]

# AFRIQUE ANGLOPHONE (anglais).
CITY_POOL_AF_EN = [
    "Lagos", "Abuja", "Ibadan", "Port Harcourt", "Kano", "Accra", "Kumasi", "Nairobi",
    "Mombasa", "Johannesburg", "Cape Town", "Durban", "Pretoria", "Kampala", "Dar es Salaam",
    "Lusaka", "Harare", "Gaborone", "Windhoek", "Addis Ababa",
]

# Pool AUTOPILOT combiné (toutes régions) — la langue + la niche sont choisies
# PAR VILLE côté hyperbetty_local (FR_CITIES → français, sinon anglais).
AUTOPILOT_POOL = CITY_POOL_FR + CITY_POOL_US + CITY_POOL_UK + CITY_POOL_AF_EN

app = Flask(__name__)
LOCK = threading.Lock()
STATUS = {"running": False, "continuous": False, "next_run": None}


def _log(line: str):
    with LOCK:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {line}\n")


# Ne pas refaire les couples (métier, ville) déjà traités récemment (efficacité :
# l'anti-doublon email évite déjà les re-contacts, ça évite les re-scans inutiles).
COVERED_FILE = OUT / "covered.csv"
COMBO_COOLDOWN_DAYS = 30


def _covered_set(cooldown_days=COMBO_COOLDOWN_DAYS):
    s = set()
    if COVERED_FILE.exists():
        for ln in COVERED_FILE.read_text().splitlines():
            p = ln.split("|")
            if len(p) >= 3:
                try:
                    if (datetime.now() - datetime.fromisoformat(p[2])).days < cooldown_days:
                        s.add(p[0] + "|" + p[1])
                except Exception:
                    s.add(p[0] + "|" + p[1])
    return s


def _mark_covered(metier, cities):
    with LOCK:
        with open(COVERED_FILE, "a") as f:
            for c in cities:
                f.write(f"{metier}|{c}|{datetime.now().isoformat()}\n")


def _run_job(cities, niche, metier, per_city, delay, resend_days, go, ollama_n, lang="", activity="", niche_fr=""):
    STATUS["running"] = True
    if go and cities:  # on note les couples (métier, ville) réellement lancés
        _mark_covered(activity or metier, cities)
    cmd = [PY, str(ROOT / "src" / "hyperbetty_local.py"),
           "--per-city", str(per_city), "--delay", str(delay),
           "--resend-days", str(resend_days), "--niche", niche, "--metier", metier]
    if lang in ("fr", "en"):
        cmd += ["--lang", lang]
    if niche_fr:
        cmd += ["--niche-fr", niche_fr]
    if activity:
        cmd += ["--activity", activity]
    if ollama_n:
        cmd += ["--ollama", str(ollama_n)]
    if cities:
        cmd += ["--cities", *cities]
    if go:
        cmd.append("--go")
    _log(f"▶️  Lancement : {' '.join(cmd)}")
    try:
        proc = subprocess.Popen(cmd, cwd=str(ROOT), stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1)
        for line in proc.stdout:
            _log(line.rstrip())
        proc.wait()
        _log(f"⏹️  Terminé (code {proc.returncode})")
    except Exception as e:
        _log(f"❌ Erreur : {e}")
    STATUS["running"] = False


_FR_SET = set(CITY_POOL_FR) | {
    "Paris", "Lyon", "Marseille", "Toulouse", "Nice", "Nantes", "Strasbourg",
    "Montpellier", "Bordeaux", "Lille", "Rennes", "Bruxelles", "Genève", "Lausanne", "Luxembourg",
}


def _continuous_loop(interval_min, per_city, resend_days, go, niche, metier, lang, pool, activity="", rotate_metiers=True):
    global _cont_idx, _met_idx
    STATUS["continuous"] = True
    # Défaut = toutes régions (petites villes FR + US + UK + Afrique). La langue
    # et la niche sont choisies PAR VILLE côté hyperbetty_local (FR→fr, sinon en).
    cities = pool if pool and len(pool) >= 3 else AUTOPILOT_POOL
    batch_n = min(3, len(cities))
    # Rotation des MÉTIERS : autopilot (bouton continu) = True. Bouton « Lancer »
    # = False → garde TON métier, avance sur les villes fraîches (anti-répétition).
    # Une activité libre reste toujours fixe.
    rotate_met = rotate_metiers and not activity
    scanned = 0  # sécurité anti-boucle-infinie quand tout est couvert
    while STATUS["continuous"]:
        if not STATUS["running"]:
            cyc_met, cyc_niche_en, cyc_niche_fr = metier, niche, niche
            if rotate_met:
                cyc_met, cyc_niche_en, cyc_niche_fr = METIER_POOL[_met_idx % len(METIER_POOL)]
                _met_idx = (_met_idx + 1) % len(METIER_POOL)
            key = activity or cyc_met
            # Villes fraîches pour CE métier : on saute les couples déjà couverts.
            covered = _covered_set()
            batch = []
            tries = 0
            while len(batch) < batch_n and tries < len(cities):
                city = cities[_cont_idx % len(cities)]
                _cont_idx = (_cont_idx + 1) % len(cities)
                tries += 1
                if f"{key}|{city}" not in covered and city not in batch:
                    batch.append(city)
            if batch:
                scanned = 0
                # Langue + niche décidées PAR VILLE côté hyperbetty (on passe les 2
                # niches, lang="" → chaque ville prend sa langue via FR_CITIES).
                _log(f"🔁 Cycle auto — {cyc_met} · {', '.join(batch)}")
                _run_job(batch, cyc_niche_en, cyc_met, per_city, 8, resend_days, go, 0,
                         "", activity, cyc_niche_fr)
            else:
                scanned += 1
                _log(f"✓ {cyc_met} : déjà couvert partout, métier suivant.")
                if scanned >= len(METIER_POOL) + 1:
                    _log("✅ Tous les couples métier×ville sont couverts (30 j). En veille.")
                    scanned = 0
        secs = max(60, int(interval_min * 60))
        STATUS["next_run"] = datetime.now().timestamp() + secs
        for _ in range(secs):
            if not STATUS["continuous"]:
                return
            time.sleep(1)


@app.route("/")
def home():
    return Path(__file__).with_name("dashboard.html").read_text()


@app.route("/api/start", methods=["POST"])
def start():
    if STATUS["running"]:
        return jsonify({"error": "un run est déjà en cours"}), 409
    b = request.json or {}
    cities = [c.strip() for c in (b.get("cities") or "").splitlines() if c.strip()]
    threading.Thread(target=_run_job, args=(
        cities, b.get("niche", "real estate brokerage"), b.get("metier", "realtor"),
        int(b.get("per_city", 25)), int(b.get("delay", 8)), int(b.get("resend_days", 3)),
        bool(b.get("go")), int(b.get("ollama", 0)), b.get("lang", ""),
        b.get("activity", "")), daemon=True).start()
    return jsonify({"ok": True})


@app.route("/api/continuous", methods=["POST"])
def continuous():
    b = request.json or {}
    if b.get("stop"):
        STATUS["continuous"] = False
        _log("⏸️  Mode continu arrêté.")
        return jsonify({"ok": True})
    if STATUS["continuous"]:
        return jsonify({"error": "déjà en mode continu"}), 409
    interval_min = float(b.get("interval_min", 20))
    pool = [c.strip() for c in (b.get("cities") or "").splitlines() if c.strip()]
    rotate_met = bool(b.get("rotate_metiers", True))
    threading.Thread(target=_continuous_loop, args=(
        interval_min, int(b.get("per_city", 25)), int(b.get("resend_days", 3)),
        bool(b.get("go")), b.get("niche", "real estate brokerage"),
        b.get("metier", "realtor"), b.get("lang", ""), pool, b.get("activity", ""), rotate_met
    ), daemon=True).start()
    src = f"{len(pool)} villes sélectionnées" if pool else "villes par défaut"
    mode = "autopilot (rotation métiers)" if rotate_met else f"métier fixe ({b.get('metier')})"
    _log(f"▶️  Relance auto toutes les {interval_min:g} min — {mode}, {src}.")
    return jsonify({"ok": True})


@app.route("/api/coverage")
def coverage():
    """Ce qui a déjà été couvert (métier × ville) — pour ne pas refaire."""
    from collections import defaultdict
    per = defaultdict(set)
    if COVERED_FILE.exists():
        for ln in COVERED_FILE.read_text().splitlines():
            p = ln.split("|")
            if len(p) >= 2:
                per[p[0]].add(p[1])
    rows = sorted(([m, sorted(c)] for m, c in per.items()), key=lambda x: -len(x[1]))
    total = sum(len(c) for _, c in rows)
    grid = len(METIER_POOL) * len(CITY_POOL)
    return jsonify({
        "total": total, "grid": grid,
        "metiers": [{"metier": m, "count": len(c), "cities": c[:60]} for m, c in rows],
    })


@app.route("/api/status")
def status():
    return jsonify(STATUS)


@app.route("/api/log")
def log():
    if not LOG_FILE.exists():
        return Response("", mimetype="text/plain")
    lines = LOG_FILE.read_text().splitlines()[-300:]
    return Response("\n".join(lines), mimetype="text/plain")


@app.route("/api/history")
def history():
    rows = []
    if SENT_FILE.exists():
        with open(SENT_FILE) as f:
            for r in csv.reader(f):
                if len(r) == 2:
                    rows.append({"email": r[0], "date": r[1]})
    rows.sort(key=lambda r: r["date"], reverse=True)
    return jsonify({"total": len(rows), "rows": rows[:200]})


if __name__ == "__main__":
    print("🚀 HyperBetty dashboard : http://127.0.0.1:8765")
    app.run(host="127.0.0.1", port=8765, debug=False)
