#!/usr/bin/env python3
"""Build the player database for the reverse mock draft app.

Fetches consensus ADP (Fantasy Football Calculator) for each supported league
size and scoring format, merges with Sleeper's player database for search
metadata, and writes a single static JSON file the frontend loads.

Usage: python3 build_data.py
"""
import json
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data"
OUT.mkdir(exist_ok=True)

FFC = "https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={teams}&year=2026"
LEAGUE_SIZES = [8, 10, 12, 14]  # 16-team leagues reuse 14-team ADP
FORMATS = {"standard": "standard", "half-ppr": "half-ppr", "ppr": "ppr"}
POSITIONS = {"QB", "RB", "WR", "TE", "PK", "DEF"}


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "draft-targets/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def load_sleeper():
    path = HERE / "sleeper_players.json"
    if not path.exists():
        print("fetching sleeper players (~14MB)...")
        data = fetch("https://api.sleeper.app/v1/players/nfl")
        path.write_text(json.dumps(data))
    else:
        data = json.loads(path.read_text())
    # index by (normalized name, position) for merging
    idx = {}
    for pid, p in data.items():
        name = (p.get("full_name") or "").lower().replace(".", "").replace("'", "")
        pos = p.get("position")
        if name and pos:
            idx[(name, "PK" if pos == "K" else pos)] = {
                "sleeper_id": pid,
                "years_exp": p.get("years_exp"),
                "age": p.get("age"),
                "search_rank": p.get("search_rank"),
            }
    return idx


def norm(name):
    return name.lower().replace(".", "").replace("'", "")


def main():
    sleeper = load_sleeper()
    # canonical player list keyed by FFC player_id
    players = {}
    for fmt_key, fmt_path in FORMATS.items():
        for teams in LEAGUE_SIZES:
            data = fetch(FFC.format(fmt=fmt_path, teams=teams))
            print(f"{fmt_key} {teams}-team: {len(data['players'])} players")
            for p in data["players"]:
                if p["position"] not in POSITIONS:
                    continue
                pid = str(p["player_id"])
                entry = players.setdefault(pid, {
                    "id": pid,
                    "name": p["name"],
                    "pos": p["position"],
                    "team": p["team"],
                    "bye": p.get("bye"),
                    "adp": {},  # adp[fmt][teams] = {adp, stdev, high, low}
                })
                entry["adp"].setdefault(fmt_key, {})[str(teams)] = {
                    "adp": p["adp"],
                    "stdev": p["stdev"],
                    "high": p["high"],
                    "low": p["low"],
                }
    # merge sleeper metadata
    matched = 0
    for p in players.values():
        s = sleeper.get((norm(p["name"]), p["pos"]))
        if s:
            p["sleeper_id"] = s["sleeper_id"]
            matched += 1
    out = {
        "meta": {"sources": ["fantasyfootballcalculator.com consensus ADP", "sleeper.app player DB"],
                 "league_sizes": LEAGUE_SIZES, "formats": list(FORMATS)},
        "players": sorted(players.values(),
                          key=lambda p: p["adp"].get("standard", {}).get("12", {}).get("adp", 999)),
    }
    (OUT / "players.json").write_text(json.dumps(out))
    print(f"wrote {len(players)} players ({matched} matched to sleeper) -> data/players.json")


if __name__ == "__main__":
    main()
