// bay.js
// The debrief, as an edit bay.
//
// The recording and the marked-up answer become one thing: while the answer plays, the word being said lights up in the text, and the notes whose marks are playing light up beside it.
// Any word can be clicked to hear it.
// A seek on the tape brings the mark it lands on into view.
// A radar shows the shape of this answer against the session's average, morphing between answers.
// Scores count up, the verdict arrives word by word, the coaching cards surface as they scroll in, and every "try instead" line can be heard aloud.
//
// It wraps the app's own drawing functions and adds to what they draw; the report, the tape and every control behave as before.

import {morphInto, blip} from "./shell.js";

const $ = (id) => document.getElementById(id);
const wrap = (name, after, before) => {
  const orig = window[name];
  if (typeof orig !== "function") return;
  window[name] = function(...args){
    let pre; try { pre = before && before(...args); } catch (_) {}
    const out = orig.apply(this, args);
    try { after(pre, ...args); } catch (e) { /* the bay must never break the report */ }
    return out;
  };
};
const ease = (x) => 1 - Math.pow(1 - x, 4);

// Counting up
function countUp(el, ms = 1100){
  if (!el) return;
  const node = [...el.childNodes].find(n => n.nodeType === 3 && /\d/.test(n.textContent));
  if (!node) return;
  const to = parseFloat(node.textContent), t0 = performance.now();
  if (!isFinite(to)) return;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / ms);
    node.textContent = String(Math.round(to * ease(k)));
    if (k < 1 && node.isConnected) requestAnimationFrame(step); else node.textContent = String(Math.round(to));
  };
  requestAnimationFrame(step);
}

// The radar
const DIMS = [["relevance", "Relevance"], ["depth", "Depth"], ["clarity", "Clarity"], ["structure", "Structure"]];
const radar = document.createElement("div");
radar.className = "radar"; radar.hidden = true;
radar.innerHTML = `<div class="ph">The shape of this answer</div>
  <svg viewBox="-36 0 312 212" role="img" aria-label="Scores for this answer on four dimensions, against the session average">
    <g class="rings"></g><g class="axes"></g>
    <polygon class="avg" points=""/><polygon class="this" points=""/><g class="pts"></g><g class="labels"></g>
  </svg>
  <div class="rkey"><span class="this">This answer</span><span class="avg">Session average</span></div>`;
$("dims").before(radar);
const CX = 120, CY = 104, RR = 72;
const axis = (k, v) => { const a = -Math.PI / 2 + k * Math.PI / 2; return [CX + Math.cos(a) * RR * v / 5, CY + Math.sin(a) * RR * v / 5]; };
(() => {
  const s = radar.querySelector("svg");
  let rings = "", axes = "";
  for (let v = 1; v <= 5; v++) rings += `<polygon points="${DIMS.map((_, k) => axis(k, v).join(",")).join(" ")}"/>`;
  DIMS.forEach((_, k) => { const [x, y] = axis(k, 5); axes += `<line x1="${CX}" y1="${CY}" x2="${x}" y2="${y}"/>`; });
  s.querySelector(".rings").innerHTML = rings; s.querySelector(".axes").innerHTML = axes;
})();
let rNow = [0, 0, 0, 0], rWant = [0, 0, 0, 0], aNow = [0, 0, 0, 0], aWant = [0, 0, 0, 0], rVel = [0, 0, 0, 0], rRaf = 0;
function drawRadar(){
  const s = radar.querySelector("svg");
  const poly = (vals) => vals.map((v, k) => axis(k, Math.max(0.15, v)).map(n => n.toFixed(1)).join(",")).join(" ");
  s.querySelector(".this").setAttribute("points", poly(rNow));
  s.querySelector(".avg").setAttribute("points", poly(aNow));
  s.querySelector(".pts").innerHTML = rNow.map((v, k) => { const [x, y] = axis(k, Math.max(0.15, v)); return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="3.2"/>`; }).join("");
  s.querySelector(".labels").innerHTML = DIMS.map(([, name], k) => {
    const [x, y] = axis(k, 6.1), anchor = k === 1 ? "start" : k === 3 ? "end" : "middle";
    const dy = k === 0 ? -2 : k === 2 ? 12 : 4;
    return `<text x="${(x + (k === 1 ? -6 : k === 3 ? 6 : 0)).toFixed(1)}" y="${(y + dy).toFixed(1)}" text-anchor="${anchor}">${name} <tspan>${rWant[k] ? rWant[k].toFixed(1) : "-"}</tspan></text>`;
  }).join("");
}
function radarTick(){
  let moving = false;
  for (let k = 0; k < 4; k++){
    rVel[k] = (rVel[k] + (rWant[k] - rNow[k]) * 0.12) * 0.76; rNow[k] += rVel[k];
    aNow[k] += (aWant[k] - aNow[k]) * 0.1;
    if (Math.abs(rWant[k] - rNow[k]) > 0.005 || Math.abs(rVel[k]) > 0.005 || Math.abs(aWant[k] - aNow[k]) > 0.005) moving = true;
  }
  drawRadar();
  rRaf = moving ? requestAnimationFrame(radarTick) : 0;
}
wrap("drawDims", (_, a) => {
  const d = (a && a.dims) || {};
  const has = DIMS.some(([k]) => d[k] != null);
  radar.hidden = !has;
  $("dims").classList.toggle("byradar", has);
  if (!has) return;
  rWant = DIMS.map(([k]) => +d[k] || 0);
  const all = (viewData && viewData.answers || []).filter(x => x.dims && !x.non_answer);
  aWant = DIMS.map(([k]) => all.length ? all.reduce((s, x) => s + (+x.dims[k] || 0), 0) / all.length : 0);
  if (!rRaf) rRaf = requestAnimationFrame(radarTick);
});

// The words, on the clock The marked-up text and the recording's word timings are lined up once per answer.
// The page's text is walked (leaving out the filler and proof tags the markup adds), so a character in the transcript can be turned into a place on the page and back.
let segs = [], words = [], litIdx = -1, hoverIdx = -1, playRaf = 0;
const HL = typeof CSS !== "undefined" && CSS.highlights && typeof Highlight !== "undefined";
function walk(){
  segs = []; let pos = 0;
  const said = $("said"), tw = document.createTreeWalker(said, NodeFilter.SHOW_TEXT, {
    acceptNode: (n) => n.parentElement.closest(".gap, .proves, svg, .empty") ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT});
  let text = "";
  for (let n = tw.nextNode(); n; n = tw.nextNode()){ segs.push({node: n, start: pos, end: pos + n.length}); pos += n.length; text += n.textContent; }
  return text;
}
const norm = (w) => w.toLowerCase().replace(/[^a-z0-9]/g, "");
function align(){
  words = []; litIdx = -1;
  if (!viewData || current !== "done") return;
  const text = walk(), timed = (typeof tapeWords !== "undefined" && tapeWords) || [];
  const tokens = [...text.matchAll(/[A-Za-z0-9][A-Za-z0-9'’-]*/g)].map(m => ({c0: m.index, c1: m.index + m[0].length, n: norm(m[0])}));
  let j = 0;
  for (const w of timed){
    const n = norm(w.word || "");
    if (!n) continue;
    for (let k = j; k < Math.min(tokens.length, j + 7); k++){
      if (tokens[k].n === n){ tokens[k].t0 = w.start; tokens[k].t1 = w.end; j = k + 1; break; }
    }
  }
  words = tokens;
  $("said").classList.toggle("timed", words.some(w => w.t0 != null));
}
function rangeOf(c0, c1){
  const a = segs.find(s => c0 >= s.start && c0 < s.end), b = segs.find(s => c1 > s.start && c1 <= s.end);
  if (!a || !b) return null;
  const r = document.createRange();
  r.setStart(a.node, c0 - a.start); r.setEnd(b.node, c1 - b.start);
  return r;
}
function light(name, idx){
  if (!HL) return;
  const w = words[idx];
  const r = w ? rangeOf(w.c0, w.c1) : null;
  if (r) CSS.highlights.set(name, new Highlight(r)); else CSS.highlights.delete(name);
}
function wordAt(t){
  let best = -1;
  for (let i = 0; i < words.length; i++){ const w = words[i]; if (w.t0 == null) continue; if (w.t0 <= t + 0.04) best = i; else break; }
  if (best >= 0 && t - (words[best].t1 ?? words[best].t0) > 1.2) return -1;
  return best;
}

// Notes whose marks are playing.
let playingIds = new Set();
function markAlong(t){
  const a = viewData && viewData.answers[selected];
  if (!a) return;
  const now = new Set((a.marks || []).concat(a.gaze_marks || []).filter(m => m.t0 != null && t >= m.t0 - 0.05 && t <= (m.t1 ?? m.t0 + 1.2)).map(m => m.id));
  for (const id of playingIds) if (!now.has(id)) document.querySelectorAll(`#said [data-id="${id}"], #notes [data-id="${id}"]`).forEach(el => el.classList.remove("playing"));
  for (const id of now) if (!playingIds.has(id)) document.querySelectorAll(`#said [data-id="${id}"], #notes [data-id="${id}"]`).forEach(el => el.classList.add("playing"));
  playingIds = now;
}
function follow(){
  playRaf = 0;
  if (current !== "done" || !viewData) return;
  const t = audio.currentTime || 0, i = wordAt(t);
  if (i !== litIdx){ litIdx = i; light("bay-now", i); }
  markAlong(t);
  if (!audio.paused) playRaf = requestAnimationFrame(follow);
}
const kick = () => { if (!playRaf) playRaf = requestAnimationFrame(follow); };
audio.addEventListener("play", kick); audio.addEventListener("timeupdate", kick); audio.addEventListener("seeked", kick);
audio.addEventListener("pause", () => { kick(); });

// A seek made on the tape brings the mark it lands on into view.
let tapeSeekAt = 0;
$("tape").addEventListener("pointerdown", () => { tapeSeekAt = performance.now(); }, true);
audio.addEventListener("seeked", () => {
  if (performance.now() - tapeSeekAt > 1500) return;
  requestAnimationFrame(() => {
    const t = audio.currentTime || 0, i = wordAt(t);
    const el = document.querySelector("#said .playing") || (i >= 0 && rangeOf(words[i].c0, words[i].c1));
    const box = el && (el.getBoundingClientRect ? el.getBoundingClientRect() : null);
    const pane = $("reportPane"), pr = pane.getBoundingClientRect();
    if (box && (box.top < pr.top + 60 || box.bottom > pr.bottom - 40)) pane.scrollTo({top: pane.scrollTop + box.top - pr.top - pr.height * 0.35, behavior: "smooth"});
  });
});

// Any word can be clicked to hear it; the one under the pointer is lit.
function charAt(x, y){
  let node, off;
  if (document.caretPositionFromPoint){ const p = document.caretPositionFromPoint(x, y); if (!p) return -1; node = p.offsetNode; off = p.offset; }
  else if (document.caretRangeFromPoint){ const r = document.caretRangeFromPoint(x, y); if (!r) return -1; node = r.startContainer; off = r.startOffset; }
  const s = segs.find(g => g.node === node);
  return s ? s.start + off : -1;
}
function tokenAt(e){
  if (e.target.closest(".gap, .proves")) return -1;
  const c = charAt(e.clientX, e.clientY);
  if (c < 0) return -1;
  const i = words.findIndex(w => c >= w.c0 && c <= w.c1);
  return i >= 0 && words[i].t0 != null ? i : -1;
}
$("said").addEventListener("pointermove", (e) => {
  const i = tokenAt(e);
  if (i !== hoverIdx){ hoverIdx = i; light("bay-hover", i); $("said").classList.toggle("onword", i >= 0); }
});
$("said").addEventListener("pointerleave", () => { hoverIdx = -1; light("bay-hover", -1); $("said").classList.remove("onword"); });
$("said").addEventListener("click", (e) => {
  // Marks already play from their own start; plain words play from themselves.
  if (e.target.closest("[data-id]")) return;
  const i = tokenAt(e);
  if (i >= 0){ seek(words[i].t0, true); blip("tick"); }
});
wrap("drawTape", () => { align(); kick(); });

// Hearing the better line
const canSpeak = "speechSynthesis" in window;
let speakingBtn = null;
function voice(){
  const vs = speechSynthesis.getVoices();
  return vs.find(v => /en-GB/i.test(v.lang) && /female|hazel|susan|libby|sonia/i.test(v.name)) || vs.find(v => /en-GB/i.test(v.lang)) || vs.find(v => /^en/i.test(v.lang)) || null;
}
function addHear(){
  if (!canSpeak) return;
  document.querySelectorAll("#notes .try, #coach .try").forEach(tr => {
    if (tr.querySelector(".hear")) return;
    const b = document.createElement("button");
    b.type = "button"; b.className = "hear";
    b.innerHTML = `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h2M7 8v8M11 5v14M15 9v6M19 11v2"/></svg><span>Hear it</span>`;
    b.addEventListener("click", (e) => {
      e.stopPropagation();
      if (speakingBtn === b){ speechSynthesis.cancel(); return; }
      speechSynthesis.cancel();
      const text = [...tr.childNodes].filter(n => !(n.nodeType === 1 && (n.tagName === "B" || n.classList.contains("hear")))).map(n => n.textContent).join(" ").trim();
      const u = new SpeechSynthesisUtterance(text), v = voice();
      if (v) u.voice = v;
      u.rate = 0.98; u.pitch = 1;
      u.onstart = () => { speakingBtn = b; b.classList.add("on"); b.querySelector("span").textContent = "Stop"; };
      u.onend = u.onerror = () => { if (speakingBtn === b) speakingBtn = null; b.classList.remove("on"); b.querySelector("span").textContent = "Hear it"; };
      if (!audio.paused) audio.pause();
      speechSynthesis.speak(u);
    });
    tr.appendChild(b);
  });
}

// Arrivals
const seen = new IntersectionObserver((es) => es.forEach(e => { if (e.isIntersecting){ e.target.classList.add("seen"); seen.unobserve(e.target); } }), {root: $("reportPane"), threshold: 0.12});
function surface(){
  document.querySelectorAll("#coach > .card, #sessionNotes > *").forEach((c, i) => { c.classList.add("pre"); c.style.setProperty("--k", i % 4); seen.observe(c); });
}
let lastVerdict = "";
function verdictIn(){
  const v = $("verdict"), text = v.textContent;
  if (!text || text === lastVerdict || v.classList.contains("long")){ lastVerdict = text; return; }
  lastVerdict = text;
  v.setAttribute("aria-label", text);
  v.innerHTML = text.split(/(\s+)/).map((w, i) => /^\s+$/.test(w) ? w : `<span class="vw" aria-hidden="true" style="--i:${i / 2}">${w.replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]))}</span>`).join("");
}

wrap("renderView", () => {
  verdictIn();
  countUp($("overallN"), 1300);
  requestAnimationFrame(() => requestAnimationFrame(() => morphInto($("answerHead"))));
});

wrap("selectAnswer", (prev, i) => {
  countUp($("answerHead").querySelector(".sc"), 900);
  addHear();
  surface();
  if (HL){ CSS.highlights.delete("bay-now"); CSS.highlights.delete("bay-hover"); }
  playingIds = new Set();
  align();
  if (prev != null && prev !== i){
    const dir = i > prev ? 1 : -1;
    for (const el of [$("answerHead"), document.querySelector("#results .doc"), $("coach")]){
      el.animate([{transform: `translate3d(${dir * 34}px,0,0)`, opacity: 0, filter: "blur(6px)"}, {transform: "none", opacity: 1, filter: "blur(0)"}],
        {duration: 560, easing: "cubic-bezier(.16,1,.3,1)"});
    }
    blip("tick");
  }
}, () => selected);
