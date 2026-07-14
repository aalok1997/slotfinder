#!/usr/bin/env python3
"""Build the consensus player database for SlotFinder.

Pulls every reachable public rankings/ADP source and blends them into one
consensus draft position per player, per scoring format, per league size:

  1. Sleeper ADP           (api.sleeper.com projections — weighted 1.5x,
                            drafts happen on Sleeper)
  2. FantasyPros ECR       (consensus of 100+ expert ranking sets, incl. the
                            major networks; has per-player expert disagreement)
  3. ESPN                  (live-draft ADP + editorial STANDARD/PPR ranks)
  4. FantasyFootballCalculator (real mock-draft ADP with per-player stdev;
                            only source with per-league-size splits)

Blend = weighted mean in overall-pick units. Volatility (stdev) = the widest of
FFC's observed draft stdev, FantasyPros' expert disagreement, and the spread
between sources — so players the sources argue about are modeled as risky.

Usage: python3 build_data.py
"""
import json
import re
import statistics
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "data"
OUT.mkdir(exist_ok=True)

LEAGUE_SIZES = ["8", "10", "12", "14"]  # 16-team leagues reuse 14-team ADP
FORMATS = ["standard", "half-ppr", "ppr"]
WEIGHTS = {"sleeper": 1.5, "fantasypros": 1.0, "espn": 1.0, "ffc": 1.0}
SUFFIXES = re.compile(r"\s+(jr|sr|ii|iii|iv|v)$")


def fetch(url, headers=None):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()


def norm_name(name):
    n = name.lower().replace(".", "").replace("'", "").replace("-", " ").strip()
    return SUFFIXES.sub("", n)


def norm_pos(pos):
    return {"K": "PK", "DST": "DEF", "D/ST": "DEF"}.get(pos, pos)


# city -> (abbrev, nickname); DST names vary wildly across sources
NFL_TEAMS = {
    "arizona": ("ARI", "cardinals"), "atlanta": ("ATL", "falcons"),
    "baltimore": ("BAL", "ravens"), "buffalo": ("BUF", "bills"),
    "carolina": ("CAR", "panthers"), "chicago": ("CHI", "bears"),
    "cincinnati": ("CIN", "bengals"), "cleveland": ("CLE", "browns"),
    "dallas": ("DAL", "cowboys"), "denver": ("DEN", "broncos"),
    "detroit": ("DET", "lions"), "green bay": ("GB", "packers"),
    "houston": ("HOU", "texans"), "indianapolis": ("IND", "colts"),
    "jacksonville": ("JAX", "jaguars"), "kansas city": ("KC", "chiefs"),
    "las vegas": ("LV", "raiders"), "los angeles chargers": ("LAC", "chargers"),
    "los angeles rams": ("LAR", "rams"), "miami": ("MIA", "dolphins"),
    "minnesota": ("MIN", "vikings"), "new england": ("NE", "patriots"),
    "new orleans": ("NO", "saints"), "new york giants": ("NYG", "giants"),
    "new york jets": ("NYJ", "jets"), "philadelphia": ("PHI", "eagles"),
    "pittsburgh": ("PIT", "steelers"), "san francisco": ("SF", "49ers"),
    "seattle": ("SEA", "seahawks"), "tampa bay": ("TB", "buccaneers"),
    "tennessee": ("TEN", "titans"), "washington": ("WAS", "commanders"),
}


def def_key(name):
    """Collapse 'Seattle Defense' / 'Seahawks D/ST' / 'Seattle Seahawks' -> SEA."""
    n = norm_name(name)
    for city, (abbr, nick) in NFL_TEAMS.items():
        if city in n or nick in n or n.startswith(abbr.lower() + " "):
            return (f"{abbr.lower()} defense", "DEF")
    return (n, "DEF")


def key(name, pos):
    if norm_pos(pos) == "DEF":
        return def_key(name)
    return (norm_name(name), norm_pos(pos))


# ---------------------------------------------------------------- sources ---
# each returns {key: {fmt: pick_value, ...}} plus optional extras per key


def src_ffc():
    """Real mock-draft ADP; the only per-league-size source. Also bye weeks."""
    out, per_size, extras = {}, {}, {}
    for fmt in FORMATS:
        for size in LEAGUE_SIZES:
            data = json.loads(fetch(
                f"https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={size}&year=2026"))
            for p in data["players"]:
                k = key(p["name"], p["position"])
                per_size.setdefault(k, {}).setdefault(fmt, {})[size] = p["adp"]
                if size == "12":
                    out.setdefault(k, {})[fmt] = p["adp"]
                    extras.setdefault(k, {}).update(
                        name=p["name"], pos=norm_pos(p["position"]),
                        team=p["team"], bye=p.get("bye"), stdev=p["stdev"])
    print(f"ffc: {len(out)} players")
    return out, per_size, extras


def src_sleeper():
    """Sleeper platform ADP from the undocumented projections endpoint."""
    pos_q = "&".join(f"position%5B%5D={p}" for p in ["QB", "RB", "WR", "TE", "K", "DEF"])
    rows = json.loads(fetch(
        f"https://api.sleeper.com/projections/nfl/2026?season_type=regular&{pos_q}&order_by=adp_std"))
    fields = {"standard": "adp_std", "half-ppr": "adp_half_ppr", "ppr": "adp_ppr"}
    out, extras = {}, {}
    for row in rows:
        pl, stats = row.get("player"), row.get("stats") or {}
        if not pl:
            continue
        name = f"{pl['first_name']} {pl['last_name']}"
        k = key(name, pl["position"])
        vals = {fmt: stats[f] for fmt, f in fields.items()
                if stats.get(f) and stats[f] < 999}
        if vals:
            out[k] = vals
            extras[k] = dict(name=name, pos=norm_pos(pl["position"]), team=pl.get("team"))
    print(f"sleeper: {len(out)} players")
    return out, extras


def src_espn():
    """ESPN live-draft ADP (PPR-leaning pool) + editorial STANDARD/PPR ranks."""
    filt = json.dumps({"players": {"limit": 400, "sortAdp": {"sortAsc": True, "sortPriority": 1}}})
    data = json.loads(fetch(
        "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/2026/segments/0/leaguedefaults/3?view=kona_player_info",
        headers={"x-fantasy-filter": filt}))
    pos_map = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "PK", 16: "DEF"}
    out, extras = {}, {}
    for row in data["players"]:
        p = row["player"]
        pos = pos_map.get(p.get("defaultPositionId"))
        if not pos:
            continue
        ranks = p.get("draftRanksByRankType", {})
        std = (ranks.get("STANDARD") or {}).get("rank")
        ppr_rank = (ranks.get("PPR") or {}).get("rank")
        adp = (p.get("ownership") or {}).get("averageDraftPosition") or 0
        ppr = adp if adp > 0 else ppr_rank
        vals = {}
        if std:
            vals["standard"] = std
        if ppr:
            vals["ppr"] = ppr
        if std and ppr:
            vals["half-ppr"] = (std + ppr) / 2
        if vals:
            k = key(p["fullName"], pos)
            out[k] = vals
            extras[k] = dict(name=p["fullName"], pos=pos, team=None)
    print(f"espn: {len(out)} players")
    return out, extras


def src_fantasypros():
    """Expert Consensus Rankings scraped from the cheat-sheet pages."""
    pages = {"standard": "consensus-cheatsheets",
             "half-ppr": "half-point-ppr-cheatsheets",
             "ppr": "ppr-cheatsheets"}
    out, extras = {}, {}
    for fmt, page in pages.items():
        html = fetch(f"https://www.fantasypros.com/nfl/rankings/{page}.php")
        m = re.search(r"var ecrData = (\{.*?\});", html, re.DOTALL)
        if not m:
            print(f"fantasypros {fmt}: ecrData not found, skipping")
            continue
        for p in json.loads(m.group(1)).get("players", []):
            pos = norm_pos(re.sub(r"\d+$", "", p.get("player_position_id", "")))
            if pos not in {"QB", "RB", "WR", "TE", "PK", "DEF"}:
                continue
            k = key(p["player_name"], pos)
            out.setdefault(k, {})[fmt] = float(p["rank_ave"])
            e = extras.setdefault(k, dict(name=p["player_name"], pos=pos,
                                          team=p.get("player_team_id")))
            e["rank_std"] = max(e.get("rank_std", 0), float(p.get("rank_std") or 0))
    print(f"fantasypros: {len(out)} players")
    return out, extras


# ------------------------------------------------------------------ blend ---

def main():
    ffc, ffc_sizes, ffc_x = src_ffc()
    slp, slp_x = src_sleeper()
    espn, espn_x = src_espn()
    fpros, fpros_x = src_fantasypros()

    sources = {"sleeper": slp, "fantasypros": fpros, "espn": espn, "ffc": ffc}
    meta_by_key = {}
    for x in (espn_x, fpros_x, slp_x, ffc_x):  # later dicts win: ffc/sleeper have teams
        for k, v in x.items():
            meta_by_key.setdefault(k, {}).update({kk: vv for kk, vv in v.items() if vv})

    all_keys = set().union(*[set(s) for s in sources.values()])
    players = []
    for k in all_keys:
        meta = meta_by_key[k]
        adp_out, src_out = {}, {}
        for fmt in FORMATS:
            vals = {name: s[k][fmt] for name, s in sources.items()
                    if k in s and fmt in s[k]}
            if not vals:
                continue
            wsum = sum(WEIGHTS[n] for n in vals)
            blend = sum(v * WEIGHTS[n] for n, v in vals.items()) / wsum
            spread = statistics.pstdev(vals.values()) if len(vals) > 1 else 0
            stdev = max(1.5, meta.get("stdev", 0), meta.get("rank_std", 0), spread)
            src_out[fmt] = {n: round(v, 1) for n, v in vals.items()}
            adp_out[fmt] = {}
            ffc_fmt = ffc_sizes.get(k, {}).get(fmt, {})
            base12 = ffc_fmt.get("12")
            for size in LEAGUE_SIZES:
                # only FFC splits by league size; shift the blend by its delta
                shift = (ffc_fmt[size] - base12) if base12 and size in ffc_fmt else 0
                adp_out[fmt][size] = {"adp": round(max(1.0, blend + shift), 1),
                                      "stdev": round(stdev, 1)}
        if not adp_out:
            continue
        best = min(v["12"]["adp"] for v in adp_out.values())
        if best > 280 and meta["pos"] not in ("PK", "DEF"):
            continue  # far off every draft board
        players.append({
            "id": f"{k[0].replace(' ', '-')}-{k[1]}".lower(),
            "name": meta["name"], "pos": meta["pos"],
            "team": meta.get("team") or "FA", "bye": meta.get("bye"),
            "adp": adp_out, "src": src_out, "nsrc": len(
                {n for n, s in sources.items() if k in s}),
        })

    players.sort(key=lambda p: p["adp"].get("standard", p["adp"][next(iter(p["adp"]))])["12"]["adp"])
    out = {
        "meta": {
            "sources": {
                "sleeper": "Sleeper platform ADP (weighted 1.5x)",
                "fantasypros": "FantasyPros Expert Consensus Rankings",
                "espn": "ESPN live-draft ADP + editorial ranks",
                "ffc": "FantasyFootballCalculator mock-draft ADP",
            },
            "weights": WEIGHTS, "league_sizes": LEAGUE_SIZES, "formats": FORMATS,
        },
        "players": players,
    }
    (OUT / "players.json").write_text(json.dumps(out))
    multi = sum(1 for p in players if p["nsrc"] >= 3)
    print(f"wrote {len(players)} players ({multi} with 3+ sources) -> data/players.json")


if __name__ == "__main__":
    main()
