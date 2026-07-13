/* SlotFinder — reverse mock draft.
 * Pick targets, we Monte-Carlo every draft slot and rank which slot
 * maximizes the value of targets you actually land. */

const BUCKETS = [
  { key: "QB",    label: "QB",    cap: 3, accepts: ["QB"] },
  { key: "RB",    label: "RB",    cap: 5, accepts: ["RB"] },
  { key: "WR",    label: "WR",    cap: 5, accepts: ["WR"] },
  { key: "TE",    label: "TE",    cap: 3, accepts: ["TE"] },
  { key: "FLEX",  label: "Flex",  cap: 3, accepts: ["RB", "WR", "TE"] },
  { key: "K",     label: "Kicker", cap: 1, accepts: ["PK"] },
  { key: "BENCH", label: "Bench", cap: 5, accepts: ["QB", "RB", "WR", "TE", "PK", "DEF"] },
];
const SIMS = 2500;

let PLAYERS = [];
let byId = new Map();
const state = {
  teams: 12,
  format: "standard",
  rounds: 10,
  targets: [], // { id, bucket }
  results: null,
  selectedSlot: null,
};

const $ = (sel) => document.querySelector(sel);

/* ---------- data ---------- */

async function loadPlayers() {
  const res = await fetch("data/players.json");
  const data = await res.json();
  PLAYERS = data.players;
  byId = new Map(PLAYERS.map((p) => [p.id, p]));
}

// ADP lookup with fallbacks: requested format/size -> same format @12 ->
// standard @ size -> anything. 16-team leagues use 14-team ADP.
function adpInfo(p, format = state.format, teams = state.teams) {
  const size = String(Math.min(teams, 14));
  const f = p.adp[format] || p.adp["standard"] || Object.values(p.adp)[0];
  if (!f) return null;
  return f[size] || f["12"] || Object.values(f)[0] || null;
}

function fmtPick(overall, teams = state.teams) {
  const round = Math.ceil(overall / teams);
  const pick = overall - (round - 1) * teams;
  return `${round}.${String(pick).padStart(2, "0")}`;
}

/* ---------- targets ---------- */

function bucketFor(pos) {
  // primary bucket, then flex, then bench — first with room
  const order = BUCKETS.filter((b) => b.accepts.includes(pos));
  for (const b of order) {
    const used = state.targets.filter((t) => t.bucket === b.key).length;
    if (used < b.cap) return b.key;
  }
  return null;
}

function addTarget(id) {
  if (state.targets.some((t) => t.id === id)) return;
  const p = byId.get(id);
  const bucket = bucketFor(p.pos);
  if (!bucket) return; // every eligible bucket full
  state.targets.push({ id, bucket });
  renderTargets();
}

function removeTarget(id) {
  state.targets = state.targets.filter((t) => t.id !== id);
  renderTargets();
}

function renderTargets() {
  const wrap = $("#buckets");
  wrap.innerHTML = "";
  for (const b of BUCKETS) {
    const mine = state.targets.filter((t) => t.bucket === b.key);
    const div = document.createElement("div");
    div.className = "bucket";
    div.innerHTML = `<h4><span>${b.label}</span><span>${mine.length}/${b.cap}</span></h4>`;
    const ul = document.createElement("ul");
    if (!mine.length) ul.innerHTML = `<li class="empty">none yet</li>`;
    for (const t of mine) {
      const p = byId.get(t.id);
      const a = adpInfo(p);
      const li = document.createElement("li");
      li.className = "chip";
      li.innerHTML =
        `<span>${p.name}</span>` +
        `<span class="adp">${a ? "ADP " + fmtPick(Math.round(a.adp)) : "—"}</span>`;
      const x = document.createElement("button");
      x.textContent = "×";
      x.setAttribute("aria-label", `Remove ${p.name}`);
      x.onclick = () => removeTarget(t.id);
      li.appendChild(x);
      ul.appendChild(li);
    }
    div.appendChild(ul);
    wrap.appendChild(div);
  }
  renderWarnings();
  $("#run").disabled = state.targets.length === 0;
}

// no more than 3 targets whose ADP lands in the same round
function renderWarnings() {
  const perRound = new Map();
  for (const t of state.targets) {
    const a = adpInfo(byId.get(t.id));
    if (!a) continue;
    const round = Math.ceil(a.adp / state.teams);
    perRound.set(round, (perRound.get(round) || 0) + 1);
  }
  const over = [...perRound].filter(([, n]) => n > 3);
  $("#warnings").innerHTML = over
    .map(
      ([r, n]) =>
        `<div class="warning-banner"><span class="icon">⚠️</span><span><strong>${n} targets project to round ${r}.</strong> You only get one pick per round — spread your targets, at most 3 per round is realistic.</span></div>`
    )
    .join("");
}

/* ---------- search ---------- */

function normalize(s) {
  return s.toLowerCase().replace(/[.'‑-]/g, "");
}

function renderSearch(q) {
  const ul = $("#search-results");
  if (!q.trim()) { ul.hidden = true; return; }
  const nq = normalize(q);
  const hits = PLAYERS.filter((p) => normalize(p.name).includes(nq)).slice(0, 8);
  ul.innerHTML = "";
  if (!hits.length) {
    ul.innerHTML = `<li class="disabled">No draftable player found — the DB covers everyone with a consensus ADP.</li>`;
  }
  for (const p of hits) {
    const a = adpInfo(p);
    const taken = state.targets.some((t) => t.id === p.id);
    const full = !taken && bucketFor(p.pos) === null;
    const li = document.createElement("li");
    if (taken || full) li.className = "disabled";
    li.innerHTML =
      `<span>${p.name}</span>` +
      `<span class="meta">${p.pos} · ${p.team} · ${a ? "ADP " + fmtPick(Math.round(a.adp)) : "no ADP"}${taken ? " · added" : full ? " · group full" : ""}</span>`;
    if (!taken && !full) {
      li.onclick = () => {
        addTarget(p.id);
        $("#search").value = "";
        ul.hidden = true;
        $("#search").focus();
      };
    }
    ul.appendChild(li);
  }
  ul.hidden = false;
}

/* ---------- simulation ---------- */

function gaussian() {
  // Box–Muller
  let u = 0, v = 0;
  while (u === 0) u = Math.random();
  while (v === 0) v = Math.random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function myPicks(slot, teams, rounds) {
  const picks = [];
  for (let r = 1; r <= rounds; r++) {
    const posInRound = r % 2 === 1 ? slot : teams + 1 - slot;
    picks.push((r - 1) * teams + posInRound);
  }
  return picks;
}

// value of landing a target: every hit is worth >=1, elite picks worth up to ~3
function targetValue(adp) {
  return 1 + Math.max(0, 200 - adp) / 100;
}

function simulate() {
  const { teams, rounds } = state;
  const targets = state.targets
    .map((t) => {
      const p = byId.get(t.id);
      const a = adpInfo(p);
      return a && { id: t.id, adp: a.adp, stdev: Math.max(a.stdev, 1.5), value: targetValue(a.adp) };
    })
    .filter(Boolean)
    .sort((x, y) => x.adp - y.adp);

  const slots = [];
  for (let slot = 1; slot <= teams; slot++) {
    const picks = myPicks(slot, teams, rounds);
    let scoreSum = 0, countSum = 0;
    const landed = new Map(targets.map((t) => [t.id, 0])); // per-target hit count
    const haul = picks.map(() => new Map()); // per-pick: playerId -> freq

    for (let s = 0; s < SIMS; s++) {
      // when would the rest of the league take each target?
      const takenAt = new Map();
      for (const t of targets) takenAt.set(t.id, Math.max(1, t.adp + gaussian() * t.stdev));
      const mine = new Set();
      for (let i = 0; i < picks.length; i++) {
        const pick = picks[i];
        const nextPick = picks[i + 1] ?? Infinity;
        const avail = targets.filter((t) => !mine.has(t.id) && takenAt.get(t.id) > pick);
        // urgency-aware: spend the pick on a target who'd be gone before our
        // next turn; otherwise wait (draft a non-target filler this round) —
        // unless we have picks to spare for everyone still alive, or it's our
        // last pick. Targets are sorted by ADP, so the first match is best value.
        const surplus = picks.length - i >= avail.length;
        const choice =
          avail.find((t) => takenAt.get(t.id) <= nextPick) ??
          (surplus || nextPick === Infinity ? avail[0] : undefined);
        if (!choice) continue;
        mine.add(choice.id);
        scoreSum += choice.value;
        countSum += 1;
        landed.set(choice.id, landed.get(choice.id) + 1);
        haul[i].set(choice.id, (haul[i].get(choice.id) || 0) + 1);
      }
    }
    slots.push({
      slot, picks,
      score: scoreSum / SIMS,
      expectedCount: countSum / SIMS,
      probs: Object.fromEntries([...landed].map(([id, n]) => [id, n / SIMS])),
      haul: haul.map((m) => {
        const top = [...m].sort((a, b) => b[1] - a[1])[0];
        // suppress sub-2% modal picks — noise, would render as "0% of sims"
        return top && top[1] / SIMS >= 0.02 ? { id: top[0], freq: top[1] / SIMS } : null;
      }),
    });
  }
  slots.sort((a, b) => b.score - a.score);
  return { slots, targets };
}

/* ---------- results rendering ---------- */

function renderResults() {
  const { slots } = state.results;
  const bySlot = [...slots].sort((a, b) => a.slot - b.slot);
  const max = Math.max(...slots.map((s) => s.score));
  const best = slots[0].slot;
  $("#results").hidden = false;
  $("#res-rounds").textContent = state.rounds;

  // bar chart: score by slot
  const chart = $("#slot-chart");
  chart.innerHTML = "";
  const bars = document.createElement("div");
  bars.className = "slot-bars";
  for (const s of bySlot) {
    const bar = document.createElement("div");
    bar.className =
      "slot-bar" +
      (s.slot === best ? " best" : "") +
      (s.slot === state.selectedSlot ? " selected" : "");
    const h = max ? (s.score / max) * 100 : 0;
    bar.innerHTML =
      (s.slot === best ? `<div class="toplabel">★</div>` : "") +
      `<div class="fill" style="height:${h}%"></div>`;
    bar.onclick = () => { state.selectedSlot = s.slot; renderResults(); };
    bar.onmousemove = (e) =>
      showTooltip(e, `Slot ${s.slot}`, `score ${s.score.toFixed(2)} · lands ${s.expectedCount.toFixed(1)} targets`);
    bar.onmouseleave = hideTooltip;
    bars.appendChild(bar);
  }
  const axis = document.createElement("div");
  axis.className = "slot-axis";
  axis.innerHTML = bySlot.map((s) => `<span>${s.slot}</span>`).join("");
  chart.appendChild(bars);
  chart.appendChild(axis);
  chart.insertAdjacentHTML("beforeend", `<div class="axis-title">draft slot (pick position in round 1)</div>`);

  // ranked cards
  const topN = state.teams >= 16 ? 5 : 3;
  const cards = $("#slot-cards");
  cards.innerHTML = "";
  slots.slice(0, topN).forEach((s, i) => {
    const card = document.createElement("div");
    card.className = "slot-card" + (s.slot === state.selectedSlot ? " selected" : "");
    card.innerHTML =
      `<div class="rank">#${i + 1} best slot</div>` +
      `<div class="slotnum">Pick ${s.slot}</div>` +
      `<div class="stat">lands ${s.expectedCount.toFixed(1)} of ${state.results.targets.length} targets</div>` +
      `<div class="stat">score ${s.score.toFixed(2)}</div>`;
    card.onclick = () => { state.selectedSlot = s.slot; renderResults(); };
    cards.appendChild(card);
  });

  renderDetail();
}

function renderDetail() {
  const { slots, targets } = state.results;
  const s = slots.find((x) => x.slot === state.selectedSlot) || slots[0];
  const el = $("#slot-detail");
  const probRows = targets
    .map((t) => ({ t, p: s.probs[t.id] || 0 }))
    .sort((a, b) => b.p - a.p)
    .map(({ t, p }) => {
      const pl = byId.get(t.id);
      return `<div class="prob-row">
        <span class="pname">${pl.name} <span class="pos-tag">${pl.pos}</span></span>
        <div class="prob-track"><div class="prob-fill" style="width:${(p * 100).toFixed(0)}%"></div></div>
        <span class="pct">${(p * 100).toFixed(0)}%</span>
      </div>`;
    })
    .join("");

  const haulRows = s.haul
    .map((h, i) => {
      const pick = s.picks[i];
      const name = h ? byId.get(h.id).name : "<em>none of your targets</em>";
      const freq = h ? `${(h.freq * 100).toFixed(0)}% of sims` : "—";
      return `<tr><td>${fmtPick(pick)}</td><td>#${pick} overall</td><td>${name}</td><td class="freq">${freq}</td></tr>`;
    })
    .join("");

  el.innerHTML = `
    <h4>Slot ${s.slot} — odds you land each target</h4>
    ${probRows}
    <h4>Slot ${s.slot} — most likely haul, round by round</h4>
    <table class="haul-table">
      <thead><tr><th>Your pick</th><th>Overall</th><th>Most frequent grab</th><th>How often</th></tr></thead>
      <tbody>${haulRows}</tbody>
    </table>`;
}

/* ---------- tooltip ---------- */

function showTooltip(e, title, sub) {
  const tt = $("#tooltip");
  tt.innerHTML = `<div class="tt-title">${title}</div><div class="tt-sub">${sub}</div>`;
  tt.hidden = false;
  const pad = 12;
  let x = e.clientX + pad, y = e.clientY + pad;
  const r = tt.getBoundingClientRect();
  if (x + r.width > window.innerWidth - 8) x = e.clientX - r.width - pad;
  if (y + r.height > window.innerHeight - 8) y = e.clientY - r.height - pad;
  tt.style.left = x + "px";
  tt.style.top = y + "px";
}
function hideTooltip() { $("#tooltip").hidden = true; }

/* ---------- wiring ---------- */

function refreshSettings() {
  state.teams = +$("#teams").value;
  state.format = $("#format").value;
  state.rounds = +$("#rounds").value;
  $("#adp-note").textContent =
    state.teams === 16
      ? "Note: 16-team consensus ADP isn't published — using 14-team ADP as the closest proxy."
      : "";
  state.results = null;
  $("#results").hidden = true;
  renderTargets();
}

async function main() {
  await loadPlayers();
  renderTargets();

  $("#teams").onchange = refreshSettings;
  $("#format").onchange = refreshSettings;
  $("#rounds").onchange = refreshSettings;

  const search = $("#search");
  search.oninput = () => renderSearch(search.value);
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".search-wrap")) $("#search-results").hidden = true;
  });

  $("#run").onclick = () => {
    state.results = simulate();
    state.selectedSlot = state.results.slots[0].slot;
    renderResults();
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  };
}

main();
