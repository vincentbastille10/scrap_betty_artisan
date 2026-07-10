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
             "Fort Worth", "Columbus", "Indianapolis", "Jacksonville", "Sacramento", "Portland"]

app = Flask(__name__)
LOCK = threading.Lock()
STATUS = {"running": False, "continuous": False, "next_run": None}


def _log(line: str):
    with LOCK:
        with open(LOG_FILE, "a") as f:
            f.write(f"[{datetime.now().strftime('%H:%M:%S')}] {line}\n")


def _run_job(cities, niche, metier, per_city, delay, resend_days, go, ollama_n):
    STATUS["running"] = True
    cmd = [PY, str(ROOT / "src" / "hyperbetty_local.py"),
           "--per-city", str(per_city), "--delay", str(delay),
           "--resend-days", str(resend_days), "--niche", niche, "--metier", metier]
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


def _continuous_loop(interval_h: float, per_city, resend_days, go, niche, metier):
    STATUS["continuous"] = True
    while STATUS["continuous"]:
        if not STATUS["running"]:
            batch = random.sample(CITY_POOL, k=min(5, len(CITY_POOL)))
            _log(f"🔁 Cycle automatique — villes : {', '.join(batch)}")
            _run_job(batch, niche, metier, per_city, 8, resend_days, go, 0)
        STATUS["next_run"] = (datetime.now().timestamp() + interval_h * 3600)
        for _ in range(int(interval_h * 3600)):
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
        int(b.get("per_city", 8)), int(b.get("delay", 8)), int(b.get("resend_days", 3)),
        bool(b.get("go")), int(b.get("ollama", 0))), daemon=True).start()
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
    interval_h = float(b.get("interval_h", 6))
    threading.Thread(target=_continuous_loop, args=(
        interval_h, int(b.get("per_city", 8)), int(b.get("resend_days", 3)),
        bool(b.get("go")), b.get("niche", "real estate brokerage"), b.get("metier", "realtor")
    ), daemon=True).start()
    _log(f"▶️  Mode continu démarré (toutes les {interval_h}h).")
    return jsonify({"ok": True})


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
