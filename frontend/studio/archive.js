// archive.js
// The quieter screens, each made into something to use rather than a list.
//
// Sessions: an archive to search and sort, each session with its date, a score dial and its takes; press one and it grows into its debrief.
// Progress: the trend can be hovered point by point and read, milestones are flagged on it, and a sentence says plainly how it has gone.
// The scores also rise as pillars of light in the room behind (app-studio.js).
// Your CV: a proof map beside it, built from every debrief on this computer: what you have proven in an answer, what came up and was never proven, and which lines have never been tested at all.
// Calendar: how ready you are for the next real interview, from your recent scores, with the days left counted down.

import {blip} from "./shell.js";

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
const wrap = (name, after) => {
  const orig = window[name];
  if (typeof orig !== "function") return;
  window[name] = function(...args){ const out = orig.apply(this, args); try { after(...args); } catch (e) { /* never break the app */ } return out; };
};
const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const getJSON = async (url) => { try { const r = await fetch(url); return r.ok ? await r.json() : null; } catch (_) { return null; } };

// Sessions
const lib = $("library");
const tools = document.createElement("div");
tools.className = "libtools";
tools.innerHTML = `<label class="search"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><input id="libFind" type="search" placeholder="Find a role" autocomplete="off"></label>
  <div class="sorts" role="radiogroup" aria-label="Order"><button type="button" role="radio" aria-checked="true" data-sort="when" class="on">Newest</button><button type="button" role="radio" aria-checked="false" data-sort="score">Best score</button><button type="button" role="radio" aria-checked="false" data-sort="takes">Most takes</button></div>
  <span class="libcount" id="libCount"></span>`;
lib.before(tools);
let sortBy = "when";
function arrange(){
  const q = $("libFind").value.trim().toLowerCase();
  const rows = [...lib.querySelectorAll("li[data-when]")];
  let shown = 0;
  rows.sort((a, b) => sortBy === "score" ? (+b.dataset.score || -1) - (+a.dataset.score || -1)
    : sortBy === "takes" ? +b.dataset.takes - +a.dataset.takes : b.dataset.when.localeCompare(a.dataset.when));
  rows.forEach((li, i) => {
    lib.appendChild(li);
    const hit = !q || li.dataset.role.includes(q);
    li.hidden = !hit; if (hit) li.style.setProperty("--k", shown++);
  });
  $("libCount").textContent = rows.length ? `${shown} of ${rows.length}` : "";
}
$("libFind").addEventListener("input", arrange);
tools.querySelectorAll(".sorts button").forEach(b => b.addEventListener("click", () => {
  sortBy = b.dataset.sort;
  tools.querySelectorAll(".sorts button").forEach(x => { x.classList.toggle("on", x === b); x.setAttribute("aria-checked", x === b); });
  blip("tick"); arrange();
}));
wrap("drawLibrary", (sessions) => {
  const rows = [...lib.children].filter(li => !li.classList.contains("empty"));
  rows.forEach((li, i) => {
    const s = sessions[i];
    if (!s) return;
    const d = s.when ? new Date(s.when) : null, ok = d && !isNaN(d);
    li.dataset.when = s.when || ""; li.dataset.score = s.overall == null ? "" : Math.round(s.overall);
    li.dataset.takes = s.takes || 0; li.dataset.role = (s.job_title || "").toLowerCase();
    li.insertAdjacentHTML("afterbegin", `<span class="date" aria-hidden="true"><b>${ok ? d.getDate() : "-"}</b><small>${ok ? MON[d.getMonth()] + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0") : ""}</small></span>`);
    const dial = s.overall == null ? `<span class="dial none" aria-hidden="true"><b>-</b></span>` : `<span class="dial" aria-hidden="true" style="--v:${Math.round(s.overall)}"><b>${Math.round(s.overall)}</b></span>`;
    li.querySelector(".right").insertAdjacentHTML("beforebegin", `<span class="takes" aria-hidden="true">${"<i></i>".repeat(Math.min(12, s.takes || 0))}</span>${dial}`);
    li.addEventListener("pointermove", (e) => { const r = li.getBoundingClientRect(); li.style.setProperty("--rx", (((e.clientY - r.top) / r.height - 0.5) * -4).toFixed(2) + "deg"); li.style.setProperty("--ry", (((e.clientX - r.left) / r.width - 0.5) * 3).toFixed(2) + "deg"); });
    li.addEventListener("pointerleave", () => { li.style.setProperty("--rx", "0deg"); li.style.setProperty("--ry", "0deg"); });
  });
  arrange();
});

// Progress
const arcline = document.createElement("p");
arcline.className = "arcline"; arcline.hidden = true;
$("trendbox").before(arcline);
const tip = document.createElement("div");
tip.className = "arctip"; tip.hidden = true;
$("trendbox").appendChild(tip);
const niceDate = (st) => { const d = new Date(st); return isNaN(d) ? (st || "") : d.toLocaleDateString(undefined, {day: "numeric", month: "short"}); };
wrap("drawTrend", (points) => {
  const svgEl = document.querySelector("#trend svg");
  if (!svgEl || points.length < 2) { arcline.hidden = true; return; }
  const W = 640, H = 220, L = 34, R = 10, T = 14, B = 26, NS = "http://www.w3.org/2000/svg";
  const x = i => L + (i / (points.length - 1)) * (W - L - R), y = v => T + (1 - Math.max(0, Math.min(100, v)) / 100) * (H - T - B);
  const scores = points.map(p => p.overall);
  const best = scores.indexOf(Math.max(...scores)), first70 = scores.findIndex(v => v >= 70);
  const flag = (i, text, up) => {
    const g = document.createElementNS(NS, "g"); g.setAttribute("class", "flag");
    const fx = x(i), fy = y(scores[i]) + (up ? -16 : 18);
    g.innerHTML = `<line x1="${fx}" y1="${y(scores[i]) + (up ? -6 : 6)}" x2="${fx}" y2="${fy + (up ? 4 : -4)}"/><text x="${fx}" y="${fy}" text-anchor="${i === points.length - 1 ? "end" : i === 0 ? "start" : "middle"}">${text}</text>`;
    svgEl.appendChild(g);
  };
  flag(best, `Best, ${Math.round(scores[best])}`, true);
  if (first70 >= 0 && first70 !== best) flag(first70, "First 70+", false);
  // Hover: a crosshair and a card for the nearest session.
  const cross = document.createElementNS(NS, "line"); cross.setAttribute("class", "cross"); cross.setAttribute("y1", T); cross.setAttribute("y2", H - B); cross.style.opacity = 0;
  const hot = document.createElementNS(NS, "circle"); hot.setAttribute("class", "hot"); hot.setAttribute("r", 8); hot.style.opacity = 0;
  const pad = document.createElementNS(NS, "rect"); pad.setAttribute("class", "arcpt"); pad.setAttribute("x", L); pad.setAttribute("y", 0); pad.setAttribute("width", W - L - R); pad.setAttribute("height", H); pad.setAttribute("fill", "transparent");
  svgEl.append(cross, hot, pad);
  let at = -1;
  pad.addEventListener("pointermove", (e) => {
    const r = svgEl.getBoundingClientRect(), vx = (e.clientX - r.left) / r.width * W;
    const i = Math.max(0, Math.min(points.length - 1, Math.round((vx - L) / (W - L - R) * (points.length - 1))));
    if (i === at) return;
    at = i;
    const p = points[i], px = x(i) / W * r.width, py = y(p.overall) / H * r.height;
    cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.style.opacity = 1;
    hot.setAttribute("cx", x(i)); hot.setAttribute("cy", y(p.overall)); hot.style.opacity = 1;
    const eye = p.presence && typeof p.presence.eye_contact === "number" ? `<span class="eyes">Eye contact ${Math.round(p.presence.eye_contact * 100)}%</span>` : "";
    const prev = i ? p.overall - points[i - 1].overall : null;
    tip.innerHTML = `<small>${esc(niceDate(p.stamp))}${p.job_title ? " · " + esc(p.job_title) : ""}</small><b>${Math.round(p.overall)}</b>${prev == null ? "" : `<span class="${prev >= 0 ? "up" : "down"}">${prev >= 0 ? "+" : ""}${Math.round(prev)} on the one before</span>`}${eye}`;
    tip.hidden = false;
    tip.style.transform = `translate3d(${Math.min(r.width - 190, Math.max(0, px - 90)).toFixed(0)}px,${Math.max(0, py - 96).toFixed(0)}px,0)`;
    blip("tick");
  });
  pad.addEventListener("pointerleave", () => { at = -1; tip.hidden = true; cross.style.opacity = 0; hot.style.opacity = 0; });
  // The sentence: best, latest, and the last three against the three before.
  const last3 = scores.slice(-3), prev3 = scores.slice(-6, -3), avg = (a) => a.reduce((s, v) => s + v, 0) / a.length;
  let line = `Your best is <b>${Math.round(scores[best])}</b>, on ${esc(niceDate(points[best].stamp))}. Your latest is <b>${Math.round(scores[scores.length - 1])}</b>.`;
  if (prev3.length) { const d = avg(last3) - avg(prev3); line += ` The last three average <b>${Math.round(avg(last3))}</b>, ${Math.abs(d) < 1.5 ? "about the same as" : d > 0 ? Math.round(d) + " up on" : Math.round(-d) + " down on"} the three before.`; }
  arcline.innerHTML = line; arcline.hidden = false;
  window.dispatchEvent(new CustomEvent("callback:trend", {detail: scores}));
});

// Your CV: the proof map
const cvpane = document.querySelector("#cvpage .pane");
const map = document.createElement("aside");
map.className = "proofmap"; map.setAttribute("aria-label", "What you have proven from your CV");
map.innerHTML = `<h2>Proof map</h2><p class="sub" id="pmSub">Built from every debrief on this computer.</p><div id="pmBody"></div>`;
cvpane.appendChild(map);
const words = (t) => (t.toLowerCase().match(/[a-z0-9]{3,}/g) || []).filter(w => !/^(and|the|for|with|from|that|this|into|using|used|over|was|were|have|has)$/.test(w));
const overlap = (a, b) => { const A = new Set(words(a)), B = words(b); if (!A.size || !B.length) return 0; return B.filter(w => A.has(w)).length / Math.min(A.size, B.length); };
let mapFor = "";
async function buildMap(){
  const lib = await getJSON("/api/sessions");
  const done = ((lib && lib.sessions) || []).filter(s => s.has_result).slice(0, 12);
  const key = done.map(s => s.name).join("|") + "#" + $("cv").value.length;
  if (key === mapFor) return;
  mapFor = key;
  $("pmBody").innerHTML = `<p class="wait">Reading your debriefs…</p>`;
  const claims = new Map();
  const reports = await Promise.all(done.map(s => getJSON(`/api/sessions/${encodeURIComponent(s.name)}/report`).then(v => [s, v])));
  for (const [s, v] of reports){
    const list = (v && v.summary && v.summary.cv_claims) || (v && v.debrief && v.debrief.cv_claims) || [];
    for (const c of list){
      const k = c.claim.toLowerCase().trim();
      const e = claims.get(k) || {claim: c.claim, proven: [], missed: []};
      (c.evidenced ? e.proven : e.missed).push(s);
      claims.set(k, e);
    }
  }
  const all = [...claims.values()];
  const proven = all.filter(c => c.proven.length), missed = all.filter(c => !c.proven.length);
  const cvLines = $("cv").value.split("\n").map(l => l.replace(/^[\s\-*•●]+/, "").trim()).filter(l => l.length > 24 && /\d|led|built|managed|created|delivered|developed|improved|designed|ran|worked/i.test(l));
  const untested = cvLines.filter(l => !all.some(c => overlap(l, c.claim) >= 0.5)).slice(0, 8);
  const chips = (list) => list.slice(0, 4).map(s => `<button type="button" data-open="${esc(s.name)}" data-when="${esc(s.when || "")}">${esc(s.job_title || "Session")} · ${esc(niceDate(s.when))}</button>`).join("");
  const row = (c, cls) => `<li class="${cls}"><i></i><div><p>${esc(c.claim)}</p>${cls === "y" ? `<div class="from">Proven in ${chips(c.proven)}</div>` : `<div class="from">Came up in ${chips(c.missed)}</div>`}</div></li>`;
  const total = all.length + untested.length;
  $("pmSub").textContent = done.length ? `From ${done.length} ${done.length === 1 ? "debrief" : "debriefs"} on this computer.` : "No debriefs yet. Each one checks your CV against what you said.";
  $("pmBody").innerHTML = !total ? `<p class="wait">${$("cv").value.trim() ? "Nothing on your CV has been checked in a debrief yet. Run an interview and it will be." : "Paste your CV and run an interview: this fills in as your answers prove what it claims."}</p>` : `
    <div class="pmbar" role="img" aria-label="${proven.length} proven, ${missed.length} not proven, ${untested.length} never tested">
      <span class="y" style="flex:${proven.length}"></span><span class="n" style="flex:${missed.length}"></span><span class="u" style="flex:${untested.length}"></span></div>
    ${proven.length ? `<h3><b>${proven.length}</b> proven in an answer</h3><ul>${proven.map(c => row(c, "y")).join("")}</ul>` : ""}
    ${missed.length ? `<h3><b>${missed.length}</b> came up, never proven</h3><ul>${missed.map(c => row(c, "n")).join("")}</ul>` : ""}
    ${untested.length ? `<h3><b>${untested.length}</b> never tested</h3><ul>${untested.map(l => `<li class="u"><i></i><div><p>${esc(l)}</p></div></li>`).join("")}</ul>` : ""}`;
  $("pmBody").querySelectorAll("[data-open]").forEach(b => b.addEventListener("click", () => openPast(b.dataset.open, niceDate(b.dataset.when))));
  $("pmBody").querySelectorAll("li").forEach((li, i) => li.style.setProperty("--k", i));
}

// Calendar: readiness
const ready = document.createElement("section");
ready.className = "ready"; ready.setAttribute("aria-label", "How ready you are for your next interview");
document.querySelector("#calpage .calwrap").before(ready);
async function buildReady(){
  const h = await getJSON("/api/history");
  const full = ((h && h.sessions) || []).filter(s => (s.kind || "interview") === "interview" && !s.ended_early && typeof s.overall === "number");
  const last = full.slice(-3).map(s => s.overall), avg = last.length ? last.reduce((a, b) => a + b, 0) / last.length : null;
  const next = typeof calData !== "undefined" && calData.next;
  let days = null, when = "";
  if (next){
    const [hh, mm] = next.time.split(":").map(Number), d = localDate(next.date);
    const at = new Date(d.getFullYear(), d.getMonth(), d.getDate(), hh, mm);
    days = Math.max(0, (at - new Date()) / 86400000);
    when = d.toLocaleDateString(undefined, {weekday: "long", day: "numeric", month: "long"}) + " at " + next.time;
  }
  const ring = days == null ? 0 : Math.max(0.04, 1 - Math.min(days, 30) / 30);
  const R = 46, C = 2 * Math.PI * R;
  ready.innerHTML = `
    <div class="cd${days == null ? " none" : ""}">
      <svg viewBox="0 0 110 110" aria-hidden="true"><circle class="track" cx="55" cy="55" r="${R}"/><circle class="run" cx="55" cy="55" r="${R}" style="stroke-dasharray:${C};--off:${(C * (1 - ring)).toFixed(1)}"/></svg>
      <div class="num"><b>${days == null ? "-" : days < 1 ? Math.max(0, Math.round(days * 24)) : Math.floor(days)}</b><small>${days == null ? "no date" : days < 1 ? "hours to go" : Math.floor(days) === 1 ? "day to go" : "days to go"}</small></div>
    </div>
    <div class="rd">
      <h2>${next ? `${esc(next.company)}${next.role ? ", " + esc(next.role) : ""}` : "No interview in the calendar yet"}</h2>
      <p class="when">${next ? esc(when) : "Pick a day below to add one, and the practice screen counts down to it."}</p>
      <div class="meter" aria-label="${avg == null ? "No scores yet" : "Your last three interviews averaged " + Math.round(avg)}">
        <div class="rail"><span class="fill" style="--v:${avg == null ? 0 : Math.round(avg)}"></span><span class="aim" style="left:70%"><em>70</em></span></div>
        <p>${avg == null ? "Run an interview and your readiness shows here, from your latest scores." : `Your last ${last.length === 1 ? "interview" : last.length + " interviews"} ${last.length === 1 ? "scored" : "averaged"} <b>${Math.round(avg)}</b>. ${avg >= 70 ? "That is where strong answers sit: keep it warm." : next && days != null ? `About ${Math.max(1, Math.min(8, Math.ceil((70 - avg) / 6)))} more sessions before then would be a sensible aim.` : "Answers that prove a claim score 70 and up."}`}</p>
      </div>
    </div>`;
}
let calDir = 0;
$("calPrev").addEventListener("click", () => { calDir = -1; }, true);
$("calNext").addEventListener("click", () => { calDir = 1; }, true);
wrap("drawCal", () => {
  if (!calDir) return;
  $("calGrid").animate([{transform: `translate3d(${calDir * 40}px,0,0)`, opacity: 0}, {transform: "none", opacity: 1}], {duration: 460, easing: "cubic-bezier(.16,1,.3,1)"});
  calDir = 0;
});
wrap("saveCal", () => setTimeout(buildReady, 400));

// Arriving on a screen
wrap("show", (name) => {
  if (name === "cvpage") buildMap();
  if (name === "calpage") buildReady();
  if (name === "sessions" || name === "progress") { /* loadPast redraws both */ }
});
if (typeof current !== "undefined"){ if (current === "cvpage") buildMap(); if (current === "calpage") buildReady(); }
// The app may have drawn its lists before this file loaded, so draw them again through the hooks above.
if (typeof loadPast === "function") loadPast();
if (typeof loadCal === "function") loadCal();
