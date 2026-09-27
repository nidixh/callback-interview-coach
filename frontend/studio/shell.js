// shell.js
// What every screen shares.
//
// A command bar (Ctrl+K) that reaches every screen, every past debrief and every action by typing; a light in the sidebar that slides to the screen you are on; a soft spotlight that follows the pointer across panels; a morph, so the card you press grows into the screen it opens; and quiet interface sounds, off until you turn them on.
//
// Everything here presses the app's own buttons or calls the app's own functions, so nothing behaves differently from pressing them yourself.

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

// Sound Synthesised, so there are no files: a soft tick, a lower thud, a breath of air.
const SOUND_KEY = "callback-sound";
let soundOn = (() => { try { return localStorage.getItem(SOUND_KEY) === "on"; } catch (_) { return false; } })();
let actx = null;
export function blip(kind = "tick"){
  if (!soundOn) return;
  try {
    actx = actx || new (window.AudioContext || window.webkitAudioContext)();
    const t = actx.currentTime, g = actx.createGain();
    g.connect(actx.destination);
    if (kind === "air"){
      const len = Math.floor(actx.sampleRate * 0.35), buf = actx.createBuffer(1, len, actx.sampleRate), d = buf.getChannelData(0);
      for (let i = 0; i < len; i++) d[i] = (Math.random() * 2 - 1) * Math.sin(Math.PI * i / len);
      const src = actx.createBufferSource(), f = actx.createBiquadFilter();
      f.type = "bandpass"; f.frequency.setValueAtTime(600, t); f.frequency.exponentialRampToValueAtTime(2400, t + 0.3); f.Q.value = 0.8;
      src.buffer = buf; src.connect(f); f.connect(g); g.gain.value = 0.05; src.start(t);
      return;
    }
    const o = actx.createOscillator();
    o.type = "sine";
    o.frequency.setValueAtTime(kind === "thud" ? 220 : 1320, t);
    o.frequency.exponentialRampToValueAtTime(kind === "thud" ? 110 : 880, t + 0.08);
    g.gain.setValueAtTime(0.0001, t); g.gain.exponentialRampToValueAtTime(kind === "thud" ? 0.09 : 0.035, t + 0.006); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
    o.connect(g); o.start(t); o.stop(t + 0.14);
  } catch (_) { /* no audio device: stay silent */ }
}
function setSound(on){ soundOn = on; try { localStorage.setItem(SOUND_KEY, on ? "on" : "off"); } catch (_) {} if (on) blip("tick"); }

// The light in the rail
const nav = document.querySelector(".nav");
const glow = document.createElement("i");
glow.className = "railglow"; glow.setAttribute("aria-hidden", "true");
nav.appendChild(glow);
function moveGlow(){
  const on = nav.querySelector("button.on");
  if (!on){ glow.style.opacity = "0"; return; }
  glow.style.opacity = "1";
  glow.style.transform = `translate3d(0,${on.offsetTop}px,0)`;
  glow.style.height = on.offsetHeight + "px";
}
new MutationObserver(moveGlow).observe(nav, {attributes: true, subtree: true, attributeFilter: ["class"]});
addEventListener("resize", moveGlow);
requestAnimationFrame(moveGlow);
nav.addEventListener("click", () => blip("tick"));

// The spotlight
const LIT = ".card, .lib li, .doc, .trend, .cal, .calform, .note, .brief, .proofmap, .ready, .changes li, .radar, .editor";
let litEl = null;
document.addEventListener("pointermove", (e) => {
  const el = e.target.closest ? e.target.closest(LIT) : null;
  if (el !== litEl){ if (litEl) litEl.classList.remove("lit"); litEl = el; if (el) el.classList.add("lit"); }
  if (!el) return;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--mx", (e.clientX - r.left).toFixed(0) + "px");
  el.style.setProperty("--my", (e.clientY - r.top).toFixed(0) + "px");
}, {passive: true});

// The morph Press a session and remember where it was; when the debrief opens, a ghost of it flies from there to the answer and dissolves into it.
let pressed = null;
document.addEventListener("pointerdown", (e) => {
  const card = e.target.closest && e.target.closest(".lib li, .sess, .arcpt, .proofmap [data-open]");
  if (card) pressed = {rect: card.getBoundingClientRect(), at: performance.now(), label: (card.querySelector(".role, b") || card).textContent.trim().slice(0, 60)};
}, true);
export function morphInto(target){
  if (!pressed || performance.now() - pressed.at > 2500 || !target) return;
  const from = pressed.rect, label = pressed.label; pressed = null;
  const to = target.getBoundingClientRect();
  if (!to.width) return;
  const ghost = document.createElement("div");
  ghost.className = "morph"; ghost.setAttribute("aria-hidden", "true");
  ghost.innerHTML = `<span>${esc(label)}</span>`;
  Object.assign(ghost.style, {left: from.left + "px", top: from.top + "px", width: from.width + "px", height: from.height + "px"});
  document.body.appendChild(ghost);
  blip("air");
  ghost.animate([
    {transform: "translate3d(0,0,0)", width: from.width + "px", height: from.height + "px", opacity: 1, borderRadius: "18px"},
    {transform: `translate3d(${to.left - from.left}px,${to.top - from.top}px,0)`, width: to.width + "px", height: to.height + "px", opacity: 0.9, borderRadius: "24px", offset: 0.72},
    {transform: `translate3d(${to.left - from.left}px,${to.top - from.top}px,0)`, width: to.width + "px", height: to.height + "px", opacity: 0, borderRadius: "24px"},
  ], {duration: 820, easing: "cubic-bezier(.16,1,.3,1)"}).onfinish = () => ghost.remove();
}

// The command bar
const bar = document.createElement("div");
bar.className = "cmdk"; bar.hidden = true;
bar.innerHTML = `<div class="cmdk-back"></div>
  <div class="cmdk-box" role="dialog" aria-modal="true" aria-label="Go anywhere">
    <div class="cmdk-in"><svg class="i" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg>
      <input id="cmdkInput" type="text" placeholder="Go to a screen, open a debrief, or do something" autocomplete="off" spellcheck="false" role="combobox" aria-expanded="true" aria-controls="cmdkList">
      <kbd>Esc</kbd></div>
    <ul class="cmdk-list" id="cmdkList" role="listbox"></ul>
    <div class="cmdk-foot"><span><kbd>&uarr;</kbd><kbd>&darr;</kbd> choose</span><span><kbd>Enter</kbd> go</span><span><kbd>Ctrl</kbd><kbd>K</kbd> open and close</span></div>
  </div>`;
document.body.appendChild(bar);
const input = $("cmdkInput"), list = $("cmdkList");
let items = [], shown = [], pick = 0, pastSessions = [];

const click = (id) => () => { const el = $(id); if (el && !el.disabled) el.click(); };
function goSetupThen(fn){ return () => { if (current !== "setup") $("navInterview").click(); setTimeout(() => { if (current === "setup") fn(); }, 60); }; }
function level(l){ return goSetupThen(() => { const b = document.querySelector(`#lvlSeg [data-level="${l}"]`); if (b) b.click(); $("example").click(); }); }
function build(){
  const here = typeof current !== "undefined" ? current : "";
  items = [
    {group: "Screens", label: "Interview", hint: "Set up or carry on", run: click("navInterview"), icon: "mic"},
    {group: "Screens", label: "Sessions", hint: "Everything recorded", run: click("navSessions"), icon: "list"},
    {group: "Screens", label: "Progress", hint: "How it has been going", run: click("navProgress"), icon: "trend"},
    {group: "Screens", label: "Your CV", hint: "What it claims, and what you have proven", run: click("navCv"), icon: "doc"},
    {group: "Screens", label: "Calendar", hint: "Your real interviews", run: click("navCal"), icon: "cal"},
    {group: "Do", label: "New interview", hint: "N", run: click("newBtn"), icon: "plus"},
    {group: "Do", label: "Fill with an easy example", hint: "3 questions", run: level("easy"), icon: "again"},
    {group: "Do", label: "Fill with a medium example", hint: "5 questions", run: level("medium"), icon: "again"},
    {group: "Do", label: "Fill with a hard example", hint: "8 questions", run: level("hard"), icon: "again"},
  ];
  if (here === "setup") items.push({group: "Do", label: "Start the interview", hint: "Ctrl + Enter", run: click("go"), icon: "mic"});
  if (here === "done" && typeof viewData !== "undefined" && viewData){
    viewData.answers.forEach((a, i) => items.push({group: "This debrief", label: `Answer ${a.index}: ${a.question}`, hint: a.non_answer ? "not an attempt" : String(Math.round(a.score || 0)), run: () => selectAnswer(i), icon: "pen"}));
    items.push({group: "This debrief", label: "Play this answer", hint: "", run: click("play"), icon: "play"});
  }
  for (const s of pastSessions.filter(s => s.has_result)){
    items.push({group: "Debriefs", label: `${s.job_title || "No role recorded"}`, hint: (s.when || "").replace("T", " "), run: () => openPast(s.name, shortStamp(s.when)), icon: "list"});
  }
  items.push({group: "Settings", label: soundOn ? "Turn interface sounds off" : "Turn interface sounds on", hint: soundOn ? "On" : "Off", run: () => setSound(!soundOn), icon: "wave"});
}
function score(item, q){
  if (!q) return 1;
  const hay = (item.label + " " + item.group + " " + item.hint).toLowerCase();
  let s = 0, at = 0;
  for (const word of q.toLowerCase().split(/\s+/).filter(Boolean)){
    const k = hay.indexOf(word);
    if (k < 0) return 0;
    s += (k === 0 || hay[k - 1] === " " ? 3 : 1); at += k;
  }
  return s - at * 0.001;
}
const ICONS = {mic: "i-mic", list: "i-list", trend: "i-trend", doc: "i-doc", cal: "i-cal", plus: "i-plus", again: "i-again", pen: "i-pen", play: "i-play", wave: "i-eye"};
function draw(){
  const q = input.value.trim();
  shown = items.map(it => [it, score(it, q)]).filter(([, s]) => s > 0).sort((a, b) => q ? b[1] - a[1] : 0).map(([it]) => it).slice(0, 40);
  pick = Math.min(pick, Math.max(0, shown.length - 1));
  let group = "", html = "";
  shown.forEach((it, i) => {
    if (it.group !== group){ group = it.group; html += `<li class="grp" role="presentation">${esc(group)}</li>`; }
    html += `<li role="option" id="cmdk-${i}" data-i="${i}" class="${i === pick ? "on" : ""}" aria-selected="${i === pick}"><svg class="i"><use href="#${ICONS[it.icon] || "i-reel"}"/></svg><span>${esc(it.label)}</span><em>${esc(it.hint)}</em></li>`;
  });
  list.innerHTML = html || `<li class="none">Nothing matches that.</li>`;
  input.setAttribute("aria-activedescendant", shown.length ? "cmdk-" + pick : "");
  const on = list.querySelector("li.on"); if (on) on.scrollIntoView({block: "nearest"});
}
async function open(){
  if (!bar.hidden) return;
  try { const r = await fetch("/api/sessions"); if (r.ok) pastSessions = (await r.json()).sessions || []; } catch (_) {}
  build(); input.value = ""; pick = 0; draw();
  bar.hidden = false; bar.classList.remove("out"); void bar.offsetWidth; bar.classList.add("in");
  input.focus(); blip("tick");
}
function close(){
  if (bar.hidden) return;
  bar.classList.remove("in"); bar.classList.add("out");
  setTimeout(() => { bar.hidden = true; bar.classList.remove("out"); }, 180);
}
function run(i){ const it = shown[i]; if (!it) return; close(); blip("thud"); setTimeout(() => it.run(), 30); }
input.addEventListener("input", () => { pick = 0; draw(); });
input.addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown"){ e.preventDefault(); pick = (pick + 1) % Math.max(1, shown.length); draw(); }
  else if (e.key === "ArrowUp"){ e.preventDefault(); pick = (pick - 1 + shown.length) % Math.max(1, shown.length); draw(); }
  else if (e.key === "Enter"){ e.preventDefault(); run(pick); }
  else if (e.key === "Escape"){ e.preventDefault(); e.stopPropagation(); close(); }
});
list.addEventListener("pointermove", (e) => { const li = e.target.closest("li[data-i]"); if (li && +li.dataset.i !== pick){ pick = +li.dataset.i; draw(); } });
list.addEventListener("click", (e) => { const li = e.target.closest("li[data-i]"); if (li) run(+li.dataset.i); });
bar.querySelector(".cmdk-back").addEventListener("click", close);
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && (e.key === "k" || e.key === "K")){ e.preventDefault(); if (bar.hidden) open(); else close(); }
}, true);

// A hint in the top bar, so the command bar can be found.
const hint = document.createElement("button");
hint.type = "button"; hint.className = "cmdkhint";
hint.innerHTML = `<svg class="i" viewBox="0 0 24 24"><circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/></svg><span>Go anywhere</span><kbd>Ctrl</kbd><kbd>K</kbd>`;
hint.addEventListener("click", open);
document.querySelector(".bar").appendChild(hint);

// The first visit Shown once, to someone with nothing recorded yet: the three steps of a session, and where the command bar is.
// Dismissed for good with one press.
const WELCOME_KEY = "callback-welcomed";
(async () => {
  try { if (localStorage.getItem(WELCOME_KEY)) return; } catch (_) { return; }
  let n = 0;
  try { const r = await fetch("/api/sessions"); if (r.ok) n = ((await r.json()).sessions || []).length; } catch (_) {}
  if (n) { try { localStorage.setItem(WELCOME_KEY, "1"); } catch (_) {} return; }
  const w = document.createElement("div");
  w.className = "welcome"; w.setAttribute("role", "dialog"); w.setAttribute("aria-modal", "true"); w.setAttribute("aria-labelledby", "welcomeTitle");
  w.innerHTML = `<div class="welcome-card">
    <h2 id="welcomeTitle">Three steps, about ten minutes</h2>
    <ol>
      <li style="--k:0"><b>Paste the advert</b><span>Or deal an example. The board underneath shows what it stresses, and where your CV leaves a gap.</span></li>
      <li style="--k:1"><b>Answer out loud</b><span>Space starts and stops each answer. The ring round the interviewer shows your length and catches filler words.</span></li>
      <li style="--k:2"><b>Read it back</b><span>Every answer returns marked up. Play it, click any word to hear it, and hear a better line read aloud.</span></li>
    </ol>
    <p>Press <kbd>Ctrl</kbd> <kbd>K</kbd> any time to go anywhere.</p>
    <button class="primary" type="button" id="welcomeGo">Let's start</button>
  </div>`;
  document.body.appendChild(w);
  const go = w.querySelector("#welcomeGo");
  const done = () => { try { localStorage.setItem(WELCOME_KEY, "1"); } catch (_) {} w.classList.add("out"); setTimeout(() => w.remove(), 300); if ($("jd")) $("jd").focus(); };
  go.addEventListener("click", done);
  w.addEventListener("keydown", (e) => { if (e.key === "Escape"){ e.preventDefault(); e.stopPropagation(); done(); } });
  go.focus();
})();
