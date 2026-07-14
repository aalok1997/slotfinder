# SlotFinder — the reverse mock draft

Instead of mock-drafting a team, you pick the **players you want** and SlotFinder
tells you **which draft slot** gives you the best odds of actually landing them.

## How it works

1. **Set your league** — 8/10/12/14/16 teams, Standard / Half-PPR / PPR, rounds to plan.
2. **Build a target list** — search any player (Sleeper player DB); they slot into
   position groups with Sleeper-style caps: 3 QB · 5 RB · 5 WR · 3 TE · 5 Flex · 2 K · 10 Bench.
   Overflow flows RB/WR/TE → Flex → Bench automatically.
3. **Simulate** — for every draft slot, 2,500 Monte Carlo snake drafts:
   - Each target's "taken at" pick is sampled from a normal distribution around
     consensus ADP with that player's real ADP volatility (stdev from
     FantasyFootballCalculator's live mock-draft data).
   - Your drafter is urgency-aware: it only spends a pick on a target who'd be
     gone before your next turn; otherwise it waits (you'd draft filler).
   - Score per landed target scales with ADP (elite ≈ 3×, late-rounder ≈ 1×).
4. **Results** — ranked top-3 slots (top-5 for 16-team), score-by-slot chart,
   per-target landing odds, and the most likely round-by-round haul for any slot.
   Warns when more than 3 targets project to the same round (you only pick once per round).

## Run it

```bash
python3 build_data.py        # refresh player DB + ADP (run weekly during draft season)
python3 -m http.server 8642  # then open http://localhost:8642
```

Pure static site — no build step, no backend. Deployable to any static host.

## Data sources — blended consensus

Every player's draft position is a weighted blend of four rankings sources
(per scoring format), computed by `build_data.py`:

| Source | What it is | Weight |
|---|---|---|
| **Sleeper ADP** | platform ADP from Sleeper's projections API | **1.5×** (drafts happen here) |
| **FantasyPros ECR** | consensus of 100+ expert ranking sets | 1.0× |
| **ESPN** | live-draft ADP + editorial Standard/PPR ranks | 1.0× |
| **FantasyFootballCalculator** | real mock-draft ADP, per league size | 1.0× |

Volatility (the stdev the simulator samples from) is the **widest of**: FFC's
observed draft stdev, FantasyPros' expert disagreement, and the spread between
the four sources — players the sources argue about are modeled as risky.
Hover any player chip or search result for the per-source breakdown.

- Only FFC publishes per-league-size ADP; other sources are blended at the
  12-team baseline and shifted by FFC's size delta.
- 16-team leagues use 14-team ADP (16 isn't published; overall-pick ADP transfers well).
- Team defense names are normalized across sources ("Seahawks D/ST" = "Seattle Defense").

## Roadmap

- [x] Blend multiple ranking sources (Sleeper, FantasyPros, ESPN, FFC) into a consensus
- [ ] Editable position caps ("this can become an edit")
- [ ] Dream-team mode: max-3-per-round realistic best roster builder
- [ ] Priority ordering within targets (drag to rank) instead of pure ADP-value greedy
- [ ] Shareable result links
- [ ] Take kicker/never-urgent targets as late as possible in the haul display
