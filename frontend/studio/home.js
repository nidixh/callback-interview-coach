// home.js
// The front door, as a film shot in one room.
//
// Scrolling is the only camera operator.
// The page is laid out as a list of shots (keys): each section, and each of the four beats of the pinned film, names where the camera stands, what it looks at and how the room is lit.
// Between two shots everything is blended, so the camera never cuts: it travels.
// The beats also drive what happens in the room: the advert is read and its phrases fly into the interviewer, it asks the question out loud, your answer grows into bars of sound, and the bars settle into the tape under a marked-up answer.
//
// In the first shot the interviewer watches the cursor, keeps count of how long you hold its eye, and the camera's autofocus hunts for its face and locks on.
// Around the frame, the film itself: sprocket holes and edge numbers running past as you scroll, and a timeline of the scenes to jump between.

import * as THREE from "three";
import {createStage} from "./stage.js";
import {createHead} from "./head.js";

const $ = (id) => document.getElementById(id);
const clamp = (v, a = 0, b = 1) => Math.max(a, Math.min(b, v));
const smooth = (x) => { x = clamp(x); return x * x * (3 - 2 * x); };
const mix = (a, b, t) => a + (b - a) * t;
const root = document.documentElement;
const fine = matchMedia("(pointer: fine)").matches;

gsap.registerPlugin(ScrollTrigger);
const lenis = window.Lenis ? new Lenis({lerp: 0.09, wheelMultiplier: 0.95, touchMultiplier: 1.4}) : null;
if (lenis) lenis.on("scroll", ScrollTrigger.update);
gsap.ticker.add((time) => { if (lenis) lenis.raf(time * 1000); });
gsap.ticker.lagSmoothing(0);

// Styles are written only when they change, so a still page costs nothing.
const written = new WeakMap();
function put(el, prop, val){
  let m = written.get(el);
  if (!m){ m = {}; written.set(el, m); }
  if (m[prop] === val) return;
  m[prop] = val;
  if (prop.startsWith("--")) el.style.setProperty(prop, val); else el.style[prop] = val;
}

document.querySelectorAll('a[href^="#"]').forEach(a => a.addEventListener("click", (e) => {
  const target = document.querySelector(a.getAttribute("href"));
  if (!target) return;
  e.preventDefault();
  if (lenis) lenis.scrollTo(target, {duration: 1.8, easing: (x) => 1 - Math.pow(1 - x, 4)});
  else target.scrollIntoView({behavior: "smooth"});
}));

// The headline sets itself
(function headline(){
  const h1 = $("headline"), words = [];
  for (const node of [...h1.childNodes]){
    if (node.nodeType === 3){
      const frag = document.createDocumentFragment();
      node.textContent.split(/(\s+)/).forEach(part => {
        if (!part) return;
        if (/^\s+$/.test(part)){ frag.appendChild(document.createTextNode(part)); return; }
        const s = document.createElement("span"); s.className = "word"; s.textContent = part; frag.appendChild(s); words.push(s);
      });
      node.replaceWith(frag);
    } else { node.classList.add("word"); words.push(node); }
  }
  gsap.from(words, {yPercent: 70, opacity: 0, filter: "blur(12px)", duration: 1.3, ease: "expo.out", stagger: 0.055, delay: 0.25});
  gsap.from(".hero .lede, .hero .row, .hero .promise", {y: 30, opacity: 0, duration: 1.2, ease: "expo.out", stagger: 0.09, delay: 0.75});
  setTimeout(() => h1.classList.add("drawn"), 1250);
})();

// Reveals, the island, the cursor
const reveal = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting){ e.target.classList.add("in"); reveal.unobserve(e.target); } }), {threshold: 0.18, rootMargin: "0px 0px -6% 0px"});
document.querySelectorAll(".reveal").forEach((el, i) => { el.style.transitionDelay = (i % 4) * 90 + "ms"; reveal.observe(el); });

const cursor = document.querySelector(".cursor");
const pointer = {x: innerWidth * 0.7, y: innerHeight * 0.45, seen: false};
const cx = gsap.quickTo(cursor, "x", {duration: 0.35, ease: "power3"}), cy = gsap.quickTo(cursor, "y", {duration: 0.35, ease: "power3"});
function cursorOn(){ root.classList.toggle("custom-cursor", fine); cursor.classList.toggle("on", fine && pointer.seen); }
addEventListener("pointermove", (e) => {
  pointer.x = e.clientX; pointer.y = e.clientY; pointer.seen = true;
  cx(e.clientX); cy(e.clientY); cursorOn();
  cursor.classList.toggle("hot", !!(e.target.closest && e.target.closest("a,button,.hud")));
}, {passive: true});
document.addEventListener("pointerleave", () => cursor.classList.remove("on"));

document.querySelectorAll(".magnet").forEach(el => {
  const mx = gsap.quickTo(el, "x", {duration: 0.6, ease: "elastic.out(1, 0.45)"}), my = gsap.quickTo(el, "y", {duration: 0.6, ease: "elastic.out(1, 0.45)"});
  el.addEventListener("pointermove", (e) => {
    const r = el.getBoundingClientRect();
    mx((e.clientX - r.left - r.width / 2) * 0.22); my((e.clientY - r.top - r.height / 2) * 0.3);
  });
  el.addEventListener("pointerleave", () => { mx(0); my(0); });
});

// The screens lean toward the cursor, and a sheen crosses the glass.
document.querySelectorAll(".hud").forEach(fig => {
  fig.addEventListener("pointermove", (e) => {
    const r = fig.getBoundingClientRect(), x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
    fig.style.setProperty("--ry", (x * 5).toFixed(2) + "deg"); fig.style.setProperty("--rx", (-y * 4).toFixed(2) + "deg");
    fig.querySelector(".screen").style.setProperty("--sheen", (100 - (x + 0.5) * 100).toFixed(1) + "%");
  });
  fig.addEventListener("pointerleave", () => { fig.style.setProperty("--ry", "0deg"); fig.style.setProperty("--rx", "0deg"); });
});

// The debrief card
const demo = $("demo"), notes = [...demo.querySelectorAll(".dn")], demoWave = $("demoWave"), dg = demoWave.getContext("2d");
let demoP = -1;
function drawDemo(p){
  if (Math.abs(p - demoP) < 0.004) return;
  demoP = p;
  const d = Math.min(2, devicePixelRatio || 1), W = demoWave.clientWidth, H = demoWave.clientHeight;
  if (!W) return;
  if (demoWave.width !== Math.round(W * d)){ demoWave.width = W * d; demoWave.height = H * d; }
  dg.setTransform(d, 0, 0, d, 0, 0); dg.clearRect(0, 0, W, H);
  const n = Math.floor(W / 5);
  for (let i = 0; i < n; i++){
    const v = .15 + .75 * Math.abs(Math.sin(i * .37) * Math.sin(i * .13 + 1)), h = Math.max(2, v * H), on = i / n < p;
    dg.fillStyle = !on ? "#3B2A21" : i / n > .18 && i / n < .62 ? "#FF7A3D" : i / n >= .62 ? "#7EE8C7" : "#FF6B85";
    dg.fillRect(i * 5, (H - h) / 2, 3, h);
  }
}

// The words of the film
const asked = $("asked"), askedText = asked.dataset.text;
const subs = $("subs"), subWords = subs.dataset.text.split(" ");
let askedShown = -1, subsShown = 0;
function typeAsked(n){
  if (n === askedShown) return;
  askedShown = n; asked.textContent = askedText.slice(0, n);
}
function typeSubs(n){
  if (n < subsShown){ subs.textContent = ""; subsShown = 0; }
  while (subsShown < n){
    const w = subWords[subsShown], s = document.createElement("span");
    s.className = "w" + (/^(So|basically)$/.test(w) ? " fill" : ""); s.textContent = w + " ";
    subs.appendChild(s); subsShown++;
  }
}

// The room
let stage = null, head = null, film3d = null;
try {
  stage = await createStage($("stage"), {post: true, manual: true, maxDpr: 1.5, fps: 60,
    dust: innerWidth < 800 ? 600 : 1500, bokeh: innerWidth < 800 ? 16 : 34, mood: "studio"});
} catch (err) {
  document.body.classList.add("no3d");
}

if (stage){
  head = createHead();
  stage.scene.add(head.group);
  stage.onResize((w, h, dpr) => head.setPixelRatio(dpr));
  try { await Promise.race([document.fonts.ready, new Promise(r => setTimeout(r, 1500))]); } catch (_) { /* draw with what is there */ }
  film3d = buildFilm(stage, head);
  stage.onFrame((dt, t) => { head.update(dt, t); film3d.update(dt, t); });
  requestAnimationFrame(() => $("stage").classList.add("lit"));
}

// The shots
const S = {top: $("top"), film: $("film"), how: $("how"), who: $("who"), privacy: $("privacy"), close: $("close")};
let keys = [], filmTop = 0, filmLen = 1;
function layout(){
  const vh = innerHeight, top = (el) => el.getBoundingClientRect().top + scrollY;
  const mid = (el) => top(el) + el.offsetHeight / 2 - vh / 2;
  const narrow = innerWidth < 1080, sx = narrow ? 0 : 0.2, sy = narrow ? -0.2 : 0;
  filmTop = top(S.film); filmLen = Math.max(1, S.film.offsetHeight - vh);
  const beat = (k) => filmTop + filmLen * (k + 0.5) / 4;
  const end = Math.max(document.documentElement.scrollHeight - vh, beat(3) + 10);
  keys = [
    {y: 0, name: "The studio", slug: "INT. STUDIO - NIGHT", pos: [0, 0.22, 6.1], look: [0, 0.02, 0], fov: 32, shift: [sx, sy]},
    {y: beat(0), name: "The advert", slug: "INSERT - THE ADVERT", pos: [-0.45, 0.3, 5.9], look: [-0.42, -0.05, 0], fov: 34, shift: [narrow ? 0 : 0.17, sy]},
    {y: beat(1), name: "The question", slug: "CLOSE ON - THE INTERVIEWER", pos: [0.05, 0.1, 3.6], look: [0, 0.05, 0], fov: 30, shift: [sx, sy]},
    {y: beat(2), name: "The answer", slug: "INT. STUDIO - CONTINUOUS", pos: [0, 0.3, 5.3], look: [0, -0.5, 0.4], fov: 34, shift: [narrow ? 0 : 0.19, sy]},
    {y: beat(3), name: "The marks", slug: "INSERT - THE DEBRIEF", pos: [0, 0.55, 6.8], look: [0, -0.4, 0], fov: 34, shift: [narrow ? 0 : 0.3, sy], dim: 0.3},
    {y: mid(S.how), name: "The screens", slug: "INT. EDIT BAY - NIGHT", pos: [2.9, 1.2, 8.2], look: [0, -0.2, 0], fov: 34, shift: [0, 0], dim: 0.4, beam: 0.55},
    {y: mid(S.who), name: "The cast", slug: "INT. STUDIO - LATER", pos: [-2.3, 0.35, 5.2], look: [0, 0.05, 0], fov: 32, shift: [narrow ? 0 : -0.25, sy], beam: 0.9, dim: narrow ? 0.3 : 1},
    {y: mid(S.privacy), name: "The room", slug: "INT. SEALED ROOM - NIGHT", pos: [0.6, 1.4, 8.6], look: [0, -0.4, 0], fov: 34, shift: [narrow ? 0 : -0.23, sy], box: 1, beam: 0.65, dim: narrow ? 0.4 : 1},
    {y: end, name: "Your take", slug: "INT. STUDIO - YOUR TAKE", pos: [0, 0.12, 4.8], look: [0, 0.22, 0], fov: 30, shift: [0, -0.17], spot: 1, beam: 0.85, dim: 0.55},
  ];
  buildTimeline();
}
const DEFAULTS = {dim: 1, beam: 1, spot: 0, box: 0};
function shotAt(y){
  let i = 0;
  while (i < keys.length - 2 && y >= keys[i + 1].y) i++;
  const a = keys[i], b = keys[i + 1] || a, t = smooth((y - a.y) / Math.max(1, b.y - a.y));
  const out = {i: t < 0.5 ? i : i + 1, t};
  out.pos = a.pos.map((v, k) => mix(v, b.pos[k], t));
  out.look = a.look.map((v, k) => mix(v, b.look[k], t));
  out.fov = mix(a.fov, b.fov, t);
  out.shift = a.shift.map((v, k) => mix(v, b.shift[k], t));
  for (const f of Object.keys(DEFAULTS)) out[f] = mix(a[f] ?? DEFAULTS[f], b[f] ?? DEFAULTS[f], t);
  return out;
}
// The timeline of scenes A strip along the bottom of the viewfinder: one block per shot, as long as the scroll it lasts, with a playhead.
// Press a block to cut to that scene.
const tl = $("timeline"), playhead = document.createElement("b");
playhead.className = "playhead";
let tlBlocks = [];
function buildTimeline(){
  if (tlBlocks.length !== keys.length){
    tl.textContent = ""; tlBlocks = keys.map((k, i) => {
      const b = document.createElement("button");
      b.type = "button"; b.dataset.name = k.name; b.setAttribute("aria-label", "Scene " + (i + 1) + ": " + k.name);
      b.innerHTML = "<i></i>";
      b.addEventListener("click", () => { const y = keys[i].y + 2; if (lenis) lenis.scrollTo(y, {duration: 1.6, easing: (x) => 1 - Math.pow(1 - x, 4)}); else scrollTo(0, y); });
      tl.appendChild(b);
      return b;
    });
    tl.appendChild(playhead);
  }
  const end = keys[keys.length - 1].y || 1;
  keys.forEach((k, i) => { const next = keys[i + 1] ? keys[i + 1].y : end + (end - keys[i - 1].y) * 0.35; tlBlocks[i].style.flexGrow = Math.max(1, next - k.y).toFixed(0); });
}

// The film edge Sprocket holes and edge numbers down both sides, running as you scroll.
const PITCH = 256, edges = [...document.querySelectorAll(".edge")];
edges.forEach(e => { const run = e.querySelector(".run"); for (let k = 0; k < 7; k++){ const s = document.createElement("span"); run.appendChild(s); } });
let edgeBase = -1;
function edgeCode(n, side){ const ft = 4471 + n; return side ? `CB ${String(ft).padStart(5, "0")} +${String((n * 7) % 16).padStart(2, "0")}` : `CALLBACK 250D 5219  ${ft}`; }
function runEdges(y){
  const s = y * 0.6, off = -(s % PITCH), base = Math.floor(s / PITCH);
  for (const e of edges) put(e.querySelector(".run"), "transform", `translate3d(0,${off.toFixed(1)}px,0)`);
  if (base === edgeBase) return;
  edgeBase = base;
  edges.forEach((e, side) => e.querySelectorAll(".run span").forEach((sp, k) => { sp.textContent = edgeCode(base + k, side); }));
}

// The autofocus
const af = $("af"), afRead = $("afRead"), afLab = $("afLab"), faceWorld = new THREE.Vector3(), faceTop = new THREE.Vector3();
let afLocked = false;

layout();
addEventListener("resize", () => { layout(); ScrollTrigger.refresh(); });
addEventListener("load", layout);
if (document.fonts) document.fonts.ready.then(layout);

// The eye, the question and the caption
const eyeChip = $("eyeChip"), eyeVal = $("eyeVal"), eyeHint = $("eyeHint"), facecard = $("facecard"), caption = $("caption"), askBtn = $("askMe");
const QUESTIONS = [
  "Tell me about a time you explained a technical decision to someone non-technical.",
  "Why do you want this role, and why now?",
  "Your CV says you led a group project. What did you personally do?",
  "Walk me through how you would find out why a report has become slow.",
  "What would your last manager say you most need to work on?",
];
let asking = 0, eye = 0, lastShown = -1;
function ask(){
  if (!head) return;
  const q = QUESTIONS[asking++ % QUESTIONS.length], words = q.split(" "), dur = words.length * 0.34 + 0.5;
  head.speak(dur);
  askBtn.classList.add("talking");
  caption.innerHTML = ""; const span = document.createElement("span"); caption.appendChild(span); caption.classList.add("on");
  words.forEach((w, i) => setTimeout(() => { span.textContent += (i ? " " : "") + w; }, i * 340));
  clearTimeout(ask.timer);
  ask.timer = setTimeout(() => { caption.classList.remove("on"); askBtn.classList.remove("talking"); }, dur * 1000 + 1600);
}
askBtn.addEventListener("click", ask);

// One frame of the page
const beats = [...document.querySelectorAll(".beat")], reelItems = [...document.querySelectorAll(".reel-index li")];
const tc = $("tc"), scene = $("scene"), sceneName = $("sceneName"), island = $("island");
const bars = [...document.querySelectorAll(".letterbox i")];
const navLinks = [...island.querySelectorAll("nav a")];
const eyeWorld = new THREE.Vector3(), eyeScreen = new THREE.Vector2(innerWidth * 0.7, innerHeight * 0.45);
const t0 = performance.now();
let lastSceneIndex = -1;

function frame(){
  const y = lenis ? lenis.scroll : scrollY, vh = innerHeight, vw = innerWidth;
  const shot = shotAt(y);

  // The film around the room.
  const lb = (1 - smooth(y / (vh * 0.55))).toFixed(3);
  put(bars[0], "transform", `scaleY(${lb})`); put(bars[1], "transform", `scaleY(${lb})`);
  runEdges(y);
  put(playhead, "left", (clamp(y / (keys[keys.length - 1].y || 1)) * 100).toFixed(2) + "%");
  island.classList.toggle("tuck", y > 40);
  const frames = Math.floor((performance.now() - t0) / 1000 * 24);
  const pad = (n) => String(n).padStart(2, "0");
  tc.textContent = `${pad(Math.floor(frames / 86400))}:${pad(Math.floor(frames / 1440) % 60)}:${pad(Math.floor(frames / 24) % 60)}:${pad(frames % 24)}`;
  if (shot.i !== lastSceneIndex){
    lastSceneIndex = shot.i;
    scene.textContent = "SC " + pad(shot.i + 1); sceneName.textContent = keys[shot.i].slug;
    sceneName.classList.remove("cut"); void sceneName.offsetWidth; sceneName.classList.add("cut");
    tlBlocks.forEach((b, k) => b.classList.toggle("on", k === shot.i));
    const id = shot.i === 0 ? "" : shot.i <= 4 ? "film" : ["how", "who", "privacy", "privacy"][shot.i - 5];
    navLinks.forEach(a => a.classList.toggle("on", a.getAttribute("href") === "#" + id));
  }

  // The four beats of the film.
  const P = clamp((y - filmTop) / filmLen), local = beats.map((_, k) => P * 4 - k);
  const inFilm = y > filmTop - vh * 0.5 && y < filmTop + filmLen + vh * 0.3;
  beats.forEach((b, k) => {
    const l = local[k];
    const fadeIn = k === 0 ? smooth((y - (filmTop - vh * 0.35)) / (vh * 0.4)) : smooth((l - 0.02) / 0.16);
    const fadeOut = k === 3 ? 1 - smooth((y - (filmTop + filmLen)) / (vh * 0.5)) : 1 - smooth((l - 0.82) / 0.16);
    const o = inFilm ? Math.min(fadeIn, fadeOut) : 0;
    put(b, "--o", o.toFixed(3));
    put(b, "--y", ((1 - o) * (fadeIn < 1 ? 46 : -46)).toFixed(1) + "px");
    b.classList.toggle("live", o > 0.5);
  });
  reelItems.forEach((li, k) => { put(li, "--p", clamp(local[k]).toFixed(3)); li.classList.toggle("on", local[k] > 0 && local[k] < 1); });
  typeAsked(Math.round(askedText.length * clamp((local[1] - 0.06) / 0.42)));
  typeSubs(Math.round(subWords.length * clamp((local[2] - 0.1) / 0.66)));
  const l3 = local[3];
  demo.classList.toggle("on", l3 > 0.1);
  notes.forEach((n, i) => n.classList.toggle("show", l3 > 0.22 + i * 0.14));
  drawDemo(clamp((l3 - 0.08) / 0.62));

  if (!stage){ return; }

  // Where the camera stands.
  // A tall, narrow screen sees less of the room across, so the camera stands further back.
  const far = clamp(1.3 / (vw / vh), 1, 2.3);
  stage.rig.look.set(...shot.look);
  stage.rig.pos.set(...shot.pos).sub(stage.rig.look).multiplyScalar(far).add(stage.rig.look);
  stage.rig.fov = shot.fov;
  stage.setViewShift(shot.shift[0], shot.shift[1]);
  stage.setParallax(fine ? (pointer.x / vw - 0.5) : 0, fine ? (0.5 - pointer.y / vh) : 0);
  stage.setSpot(shot.spot);
  head.dim = shot.dim;
  film3d.set({P, local, box: shot.box, beam: shot.beam, inFilm});

  // The interviewer's eye, on screen.
  head.group.updateMatrixWorld();
  eyeWorld.copy(head.eyeMid); head.group.localToWorld(eyeWorld); eyeWorld.project(stage.camera);
  eyeScreen.set((eyeWorld.x * 0.5 + 0.5) * vw, (0.5 - eyeWorld.y * 0.5) * vh);

  // Where it looks: at the cursor in the first shot, at its work in the film.
  const hero = 1 - smooth((y - vh * 0.2) / (vh * 0.6));
  let lx = clamp((pointer.x - eyeScreen.x) / (vw * 0.42), -1, 1), ly = clamp((eyeScreen.y - pointer.y) / (vh * 0.42), -1, 1);
  if (!pointer.seen){ lx = Math.sin(performance.now() / 2600) * 0.3; ly = Math.sin(performance.now() / 3700) * 0.12; }
  const beatLook = [[-0.75, -0.12], [0, 0], [0, -0.05], [0, -0.55]];
  let bx = 0, by = 0, bw = 0;
  local.forEach((l, k) => { const w = inFilm ? clamp(1 - Math.abs(l - 0.5) * 2) : 0; bx += beatLook[k][0] * w; by += beatLook[k][1] * w; bw += w; });
  const wFilm = clamp(bw * 1.6);
  head.look(mix(lx * (0.35 + hero * 0.65), bx / Math.max(bw, 0.001), wFilm), mix(ly * (0.35 + hero * 0.65), by / Math.max(bw, 0.001), wFilm));
  if (inFilm && local[1] > 0.06 && local[1] < 0.62) head.speak(0.12);
  head.mode = inFilm && local[2] > 0 && local[2] < 1 ? "listening" : inFilm && local[0] > 0 && local[0] < 1 ? "attentive" : "idle";

  // Holding its eye: the closer and longer, the higher the reading.
  const near = Math.hypot(pointer.x - eyeScreen.x, pointer.y - eyeScreen.y) < Math.max(80, vw * 0.07);
  const dt = gsap.ticker.deltaRatio(60) / 60;
  eye = clamp(eye + (pointer.seen && near && hero > 0.5 ? 0.42 : -0.28) * dt);
  const shown = Math.round(eye * 100);
  if (shown !== lastShown){
    lastShown = shown; eyeVal.textContent = shown + "%"; eyeChip.style.setProperty("--eye", eye.toFixed(3));
    const held = shown >= 85; eyeChip.classList.toggle("held", held);
    eyeHint.textContent = held ? "Holding eye contact" : shown > 0 && near ? "Keep looking" : "Look it in the eye";
  }
  facecard.classList.toggle("on", hero > 0.4 && vw > 640);
  put(facecard, "--fx", Math.min(vw - 250, eyeScreen.x + vw * 0.15).toFixed(0) + "px");
  put(facecard, "--fy", Math.min(vh - 170, eyeScreen.y + vh * 0.12).toFixed(0) + "px");
  put(caption, "--cx", eyeScreen.x.toFixed(0) + "px");
  put(caption, "--cy", Math.min(vh - 120, eyeScreen.y + vh * 0.3).toFixed(0) + "px");

  // The autofocus: brackets sized to the head, hunting until you hold its eye, then locked.
  faceWorld.set(0, -0.28, 0.2); head.group.localToWorld(faceWorld);
  const dist = faceWorld.distanceTo(stage.camera.position);
  faceTop.set(0, 0.72, 0.2); head.group.localToWorld(faceTop);
  faceWorld.project(stage.camera); faceTop.project(stage.camera);
  const fh = Math.abs(faceTop.y - faceWorld.y) * vh * 1.02, fw = fh * 0.8;
  const hunt = afLocked ? 0 : Math.sin(performance.now() / 180) * 4 * (1 - eye);
  put(af, "transform", `translate3d(${((faceWorld.x * 0.5 + 0.5) * vw - fw / 2 - hunt).toFixed(1)}px,${((0.5 - faceWorld.y * 0.5) * vh - fh / 2 - hunt).toFixed(1)}px,0)`);
  put(af, "width", (fw + hunt * 2).toFixed(0) + "px"); put(af, "height", (fh + hunt * 2).toFixed(0) + "px");
  af.classList.toggle("on", hero > 0.35);
  if (lastShown >= 85 !== afLocked){ afLocked = lastShown >= 85; af.classList.toggle("lock", afLocked); afLab.textContent = afLocked ? "AF-C  LOCKED" : "AF-C  FACE"; }
  const m = (dist * 0.1 * 2.2).toFixed(2) + " M";
  if (afRead.textContent !== m) afRead.textContent = m;
  if (hero < 0.3 && caption.classList.contains("on")) caption.classList.remove("on");
  cursor.classList.toggle("face", near && hero > 0.5);

  stage.render(performance.now());
}
gsap.ticker.add(frame);

// A click on the face asks a question too.
addEventListener("click", (e) => {
  if (!head || e.target.closest("a,button,input,textarea")) return;
  if (Math.hypot(e.clientX - eyeScreen.x, e.clientY - eyeScreen.y) < Math.max(90, innerWidth * 0.08) && scrollY < innerHeight * 0.5) ask();
});

// The pieces of the film, in 3D
function buildFilm(stage, head){
  const group = new THREE.Group();
  stage.scene.add(group);

  // The advert: a printed page, highlighted as it is read.
  const ad = drawAdvert();
  const texBase = new THREE.CanvasTexture(ad.base), texHi = new THREE.CanvasTexture(ad.hi);
  for (const t of [texBase, texHi]){ t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4; }
  const CARD_W = 1.45, CARD_H = CARD_W * ad.base.height / ad.base.width;
  const cardMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide,
    uniforms: {uBase: {value: texBase}, uHi: {value: texHi}, uSweep: {value: 0}, uAmt: {value: 0}, uLight: {value: 0.4}},
    vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */`
      uniform sampler2D uBase, uHi; uniform float uSweep, uAmt, uLight; varying vec2 vUv;
      void main(){
        vec3 c = texture2D(uBase, vUv).rgb; vec4 h = texture2D(uHi, vUv);
        float row = 1.0 - vUv.y;
        c = mix(c, h.rgb, h.a * step(row, uSweep));
        float scan = exp(-pow((row - uSweep) * 70.0, 2.0)) * step(0.01, uSweep) * step(uSweep, 0.99);
        float light = uLight * (0.5 + 0.6 * smoothstep(1.1, 0.0, distance(vUv, vec2(0.18, 0.95))));
        c = c * light + vec3(1.0, 0.5, 0.2) * scan * 0.55;
        gl_FragColor = vec4(c, uAmt);
        #include <colorspace_fragment>
      }`,
  });
  const card = new THREE.Mesh(new THREE.PlaneGeometry(CARD_W, CARD_H), cardMat);
  const cardHome = new THREE.Vector3(-1.3, -0.18, -0.3);
  card.rotation.set(-0.05, 0.34, 0.03);
  group.add(card);

  // The CV behind it, and the threads between what each one claims.
  const cv = drawCv();
  const cvTex = new THREE.CanvasTexture(cv.base); cvTex.colorSpace = THREE.SRGBColorSpace;
  const cvMat = new THREE.MeshBasicMaterial({map: cvTex, transparent: true, opacity: 0, depthWrite: false, side: THREE.DoubleSide, color: new THREE.Color(0.36, 0.34, 0.32)});
  const cvW = 1.0, cvCard = new THREE.Mesh(new THREE.PlaneGeometry(cvW, cvW * cv.base.height / cv.base.width), cvMat);
  cvCard.position.set(1.42, 0.42, -1.25); cvCard.rotation.set(-0.03, -0.52, 0.04);
  group.add(cvCard);
  const threadGeo = new THREE.BufferGeometry();
  threadGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(cv.spots.length * 6), 3));
  const threads = new THREE.LineSegments(threadGeo, new THREE.LineBasicMaterial({color: new THREE.Color("#FFC857"), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false}));
  threads.frustumCulled = false;
  group.add(threads);

  // Phrases lifted off the page, flying to the interviewer.
  const tokens = ad.spots.map((s) => {
    const tex = new THREE.CanvasTexture(pill(s.text)); tex.colorSpace = THREE.SRGBColorSpace;
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({map: tex, transparent: true, depthWrite: false, opacity: 0}));
    const aspect = tex.image.width / tex.image.height;
    sp.userData = {spot: s, aspect, done: false};
    sp.scale.set(0.15 * aspect, 0.15, 1);
    group.add(sp);
    return sp;
  });

  // The answer, as bars of sound.
  const N = 132, bars = new THREE.InstancedMesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial({toneMapped: false, transparent: true}), N);
  const amp = [], markColour = [], neutral = new THREE.Color("#E8955E");
  for (let i = 0; i < N; i++){
    let v = .15 + .75 * Math.abs(Math.sin(i * .37) * Math.sin(i * .13 + 1));
    if ((i > 31 && i < 36) || (i > 84 && i < 88)) v = 0.04;       // two pauses
    amp.push(v);
    const t = i / N;
    markColour.push(new THREE.Color(t > .18 && t < .62 ? "#FF7A3D" : t >= .62 ? "#7EE8C7" : "#FF6B85"));
    bars.setColorAt(i, neutral);
  }
  bars.frustumCulled = false;
  group.add(bars);
  const dummy = new THREE.Object3D(), tmpC = new THREE.Color();

  // The sealed room: its edges draw in, and what bounces around inside stays inside.
  const BOX = new THREE.Vector3(4.4, 3.1, 3.8), BOXC = new THREE.Vector3(0, -0.72, 0.1);
  const edges = [];
  const corners = [[-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1], [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1]];
  [[0, 1], [1, 2], [2, 3], [3, 0], [4, 5], [5, 6], [6, 7], [7, 4], [0, 4], [1, 5], [2, 6], [3, 7]].forEach(([a, b]) => {
    edges.push(...corners[a].map((v, k) => v * BOX.getComponent(k) / 2 + BOXC.getComponent(k)), 0);
    edges.push(...corners[b].map((v, k) => v * BOX.getComponent(k) / 2 + BOXC.getComponent(k)), 1);
  });
  const eArr = new Float32Array(edges), boxGeo = new THREE.BufferGeometry();
  const posE = new Float32Array(eArr.length / 4 * 3), tE = new Float32Array(eArr.length / 4);
  for (let i = 0; i < tE.length; i++){ posE.set(eArr.subarray(i * 4, i * 4 + 3), i * 3); tE[i] = eArr[i * 4 + 3]; }
  boxGeo.setAttribute("position", new THREE.BufferAttribute(posE, 3));
  boxGeo.setAttribute("aT", new THREE.BufferAttribute(tE, 1));
  const boxMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: {uDraw: {value: 0}, uColor: {value: new THREE.Color("#7EE8C7")}},
    vertexShader: `attribute float aT; varying float vT; void main(){ vT = aT; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */`
      uniform float uDraw; uniform vec3 uColor; varying float vT;
      void main(){
        float on = step(vT, uDraw);
        float tip = exp(-pow((vT - uDraw) * 18.0, 2.0));
        gl_FragColor = vec4(uColor, (on * 0.32 + tip * 0.9) * step(0.001, uDraw));
        #include <colorspace_fragment>
      }`,
  });
  const box = new THREE.LineSegments(boxGeo, boxMat);
  box.frustumCulled = false;
  group.add(box);
  const PK = 90, pkPos = new Float32Array(PK * 3), pkVel = [], pkFlash = new Float32Array(PK);
  for (let i = 0; i < PK; i++){
    for (let k = 0; k < 3; k++) pkPos[i * 3 + k] = BOXC.getComponent(k) + (Math.random() - 0.5) * BOX.getComponent(k) * 0.8;
    pkVel.push(new THREE.Vector3().randomDirection().multiplyScalar(0.6 + Math.random() * 0.9));
  }
  const pkGeo = new THREE.BufferGeometry();
  pkGeo.setAttribute("position", new THREE.BufferAttribute(pkPos, 3));
  pkGeo.setAttribute("aFlash", new THREE.BufferAttribute(pkFlash, 1));
  const pkMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: {uAmt: {value: 0}, uPix: {value: 1}, uColor: {value: new THREE.Color("#7EE8C7")}},
    vertexShader: `attribute float aFlash; uniform float uPix; varying float vF; void main(){ vF = aFlash; vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_PointSize = (2.0 + aFlash * 9.0) * uPix * (4.0 / -mv.z); gl_Position = projectionMatrix * mv; }`,
    fragmentShader: /* glsl */`
      uniform float uAmt; uniform vec3 uColor; varying float vF;
      void main(){ float d = length(gl_PointCoord - 0.5); float a = smoothstep(0.5, 0.0, d); gl_FragColor = vec4(uColor, a * a * (0.35 + vF) * uAmt);
        #include <colorspace_fragment>
      }`,
  });
  const packets = new THREE.Points(pkGeo, pkMat);
  packets.frustumCulled = false;
  group.add(packets);
  stage.onResize((w, h, dpr) => { pkMat.uniforms.uPix.value = dpr; });

  // Per frame
  let st = {P: 0, local: [-1, -1, -1, -1], box: 0, beam: 1, inFilm: false};
  const forehead = new THREE.Vector3(0, 0.5, 0.55), tmp = new THREE.Vector3(), a3 = new THREE.Vector3(), b3 = new THREE.Vector3(), ctl = new THREE.Vector3();
  const spotWorld = (s) => {
    tmp.set((s.u - 0.5) * CARD_W, (0.5 - s.v) * CARD_H, 0.01);
    return card.localToWorld(tmp.clone());
  };

  function update(dt){
    const [l0, l1, l2, l3] = st.local;
    // The advert rises into the light, is read, and leaves as the question is asked.
    const inAd = st.inFilm ? smooth((l0 + 0.25) / 0.35) * (1 - smooth((l1 - 0.1) / 0.4)) : 0;
    cardMat.uniforms.uAmt.value = inAd;
    card.visible = inAd > 0.001;
    card.position.copy(cardHome); card.position.y -= (1 - inAd) * 1.1; card.position.z -= (1 - inAd) * 0.6;
    card.rotation.y = 0.34 + (1 - inAd) * 0.4 + Math.sin(performance.now() / 2400) * 0.02;
    cardMat.uniforms.uSweep.value = smooth((l0 - 0.1) / 0.45);
    card.updateMatrixWorld();

    const cvIn = st.inFilm ? smooth((l0 - 0.2) / 0.25) * (1 - smooth((l0 - 0.85) / 0.2)) : 0;
    cvMat.opacity = cvIn * 0.95; cvCard.visible = cvIn > 0.001;
    cvCard.position.y = 0.42 - (1 - cvIn) * 0.5;
    cvCard.updateMatrixWorld();
    const tp = threadGeo.getAttribute("position").array;
    cv.spots.forEach((s, i) => {
      a3.set((s.u - 0.5) * cvW, (0.5 - s.v) * cvW * cv.base.height / cv.base.width, 0.01); cvCard.localToWorld(a3);
      b3.copy(spotWorld(ad.spots[s.to]));
      tp.set([a3.x, a3.y, a3.z, b3.x, b3.y, b3.z], i * 6);
    });
    threadGeo.getAttribute("position").needsUpdate = true;
    threads.material.opacity = cvIn * 0.6 * smooth((l0 - 0.35) / 0.2);

    // Each phrase lifts off its line once the highlighter has passed it, then flies.
    const fly = smooth((l0 - 0.5) / 0.45);
    head.group.updateMatrixWorld();
    const goal = head.group.localToWorld(forehead.clone());
    tokens.forEach((sp, i) => {
      const s = sp.userData.spot, lit = cardMat.uniforms.uSweep.value > s.v + 0.02;
      const p = clamp((fly * 1.35 - i * 0.07) / 0.75);
      const start = spotWorld(s);
      ctl.copy(start).lerp(goal, 0.5); ctl.y += 0.9; ctl.z += 0.5;
      const e = smooth(p);
      sp.position.copy(start).multiplyScalar((1 - e) * (1 - e)).addScaledVector(ctl, 2 * (1 - e) * e).addScaledVector(goal, e * e);
      sp.position.z += 0.05;
      const sz = 0.15 * (1 - e * 0.7);
      sp.scale.set(sz * sp.userData.aspect, sz, 1);
      sp.material.opacity = (lit ? 1 : 0) * inAd * (p >= 1 ? 0 : 1) * smooth(p * 8);
      sp.visible = sp.material.opacity > 0.01;
      if (p >= 1 && !sp.userData.done){ sp.userData.done = true; head.absorb(); }
      if (p < 0.98) sp.userData.done = false;
    });

    // The answer grows, left to right, then settles into the tape.
    const reveal = st.inFilm ? smooth((l2 - 0.08) / 0.75) : (st.P >= 1 ? 1 : 0);
    const flat = smooth((l3 - 0.02) / 0.4);
    const barsAmt = st.inFilm ? smooth((l2 + 0.1) / 0.2) * (1 - smooth((st.P * 4 - 3.95) / 0.3)) : 0;
    bars.visible = barsAmt > 0.001;
    if (bars.visible){
      const tNow = performance.now() / 1000;
      for (let i = 0; i < N; i++){
        const grow = smooth((reveal * N * 1.12 - i) / 7);
        const live = 1 + (flat < 0.5 && grow > 0.95 ? Math.sin(tNow * 9 + i * 0.7) * 0.06 * (1 - flat * 2) : 0);
        const h = Math.max(0.004, amp[i] * 0.62 * grow * live * mix(1, 0.24, flat));
        dummy.position.set(mix(-1.25, 1.95, i / (N - 1)), mix(-1.05, -1.5, flat), mix(0.35, 0.2, flat));
        dummy.scale.set(0.014, h, 0.014);
        dummy.updateMatrix();
        bars.setMatrixAt(i, dummy.matrix);
        tmpC.copy(neutral).lerp(markColour[i], flat).multiplyScalar(barsAmt * (0.22 + 0.4 * grow));
        bars.setColorAt(i, tmpC);
      }
      bars.instanceMatrix.needsUpdate = true; bars.instanceColor.needsUpdate = true;
      if (st.inFilm && l2 > 0 && l2 < 1){
        const front = Math.min(N - 1, Math.floor(reveal * N)); head.level = amp[front];
      }
    }

    // The sealed room.
    boxMat.uniforms.uDraw.value = st.box;
    box.visible = st.box > 0.001;
    pkMat.uniforms.uAmt.value = st.box;
    packets.visible = st.box > 0.01;
    if (packets.visible){
      for (let i = 0; i < PK; i++){
        const v = pkVel[i];
        for (let k = 0; k < 3; k++){
          let p = pkPos[i * 3 + k] + v.getComponent(k) * dt;
          const lo = BOXC.getComponent(k) - BOX.getComponent(k) / 2, hi = BOXC.getComponent(k) + BOX.getComponent(k) / 2;
          if (p < lo || p > hi){ v.setComponent(k, -v.getComponent(k)); p = Math.max(lo, Math.min(hi, p)); pkFlash[i] = 1; }
          pkPos[i * 3 + k] = p;
        }
        pkFlash[i] = Math.max(0, pkFlash[i] - dt * 2.4);
      }
      pkGeo.getAttribute("position").needsUpdate = true; pkGeo.getAttribute("aFlash").needsUpdate = true;
    }

    stage.key.material.uniforms.uAmt.value *= st.beam;
    stage.rim.material.uniforms.uAmt.value *= 0.6 + st.beam * 0.4;
  }

  return {update, set(next){ st = next; }};
}

// The paper
function drawAdvert(){
  const W = 900, H = 1170, base = document.createElement("canvas"), hi = document.createElement("canvas");
  base.width = hi.width = W; base.height = hi.height = H;
  const g = base.getContext("2d"), h = hi.getContext("2d");
  const grad = g.createLinearGradient(0, 0, W, H); grad.addColorStop(0, "#F4ECE2"); grad.addColorStop(1, "#DDD0C1");
  g.fillStyle = grad; g.fillRect(0, 0, W, H);
  for (let i = 0; i < 14000; i++){ g.fillStyle = `rgba(80,52,34,${Math.random() * 0.045})`; g.fillRect(Math.random() * W, Math.random() * H, 1.6, 1.6); }
  const spots = [];
  let y = 92;
  const X = 78, MAX = W - 2 * X;
  g.fillStyle = "#C4541F"; g.font = '600 20px "JetBrains Mono", monospace'; g.fillText("JOB ADVERT  ·  REF 4471", X, y); y += 70;
  g.fillStyle = "#1E130D"; g.font = '800 62px "Bricolage Grotesque", sans-serif';
  g.fillText("Graduate Software", X, y); y += 64; g.fillText("Engineer", X, y); y += 50;
  g.fillStyle = "#6A5244"; g.font = '500 24px "Onest", sans-serif'; g.fillText("Northwind Analytics  ·  Leeds, hybrid  ·  £32,000 to £36,000", X, y); y += 40;
  g.fillStyle = "rgba(60,36,24,.25)"; g.fillRect(X, y, MAX, 2); y += 60;
  function heading(t){ g.fillStyle = "#2A1A12"; g.font = '700 27px "Onest", sans-serif'; g.fillText(t, X, y); y += 48; }
  function bullet(parts){
    // parts: text, with [square brackets] around a phrase the questions will come from.
    const font = '400 25px "Onest", sans-serif'; g.font = font;
    const tokens = [];
    parts.split(/(\[[^\]]+\])/).forEach(p => {
      if (!p) return;
      if (p[0] === "[") tokens.push({t: p.slice(1, -1), key: true});
      else p.split(/(\s+)/).forEach(w => w && tokens.push({t: w, key: false}));
    });
    let x = X + 30, line = [];
    g.fillStyle = "#C4541F"; g.beginPath(); g.arc(X + 8, y - 8, 4.5, 0, Math.PI * 2); g.fill();
    for (const tk of tokens){
      const w = g.measureText(tk.t).width;
      if (x + w > X + MAX && tk.t.trim()){ x = X + 30; y += 38; }
      if (!tk.t.trim() && x === X + 30) continue;
      g.fillStyle = "#3A2619"; g.fillText(tk.t, x, y);
      if (tk.key){
        h.save(); h.fillStyle = "rgba(255,196,72,.72)"; h.beginPath();
        h.moveTo(x - 5, y - 24); h.lineTo(x + w + 5, y - 27); h.lineTo(x + w + 7, y + 7); h.lineTo(x - 4, y + 9); h.closePath(); h.fill();
        h.globalCompositeOperation = "source-over"; h.fillStyle = "#1E130D"; h.font = font; h.fillText(tk.t, x, y); h.restore();
        spots.push({text: tk.t, u: (x + w / 2) / W, v: (y - 9) / H});
      }
      x += w;
    }
    y += 50;
  }
  heading("What you will do");
  bullet("Build and maintain [Python services] behind our reporting tools.");
  bullet("Explain [technical decisions] to non-technical [stakeholders].");
  bullet("Write and tune [SQL] against large datasets.");
  bullet("Join a supportive [on-call rota] after six months.");
  y += 14; heading("What we are looking for");
  bullet("A degree in a numerate subject, or equivalent experience.");
  bullet("Clear written and spoken [communication].");
  bullet("Curiosity about how data is used to make decisions.");
  return {base, hi, spots};
}

function drawCv(){
  const W = 700, H = 900, base = document.createElement("canvas");
  base.width = W; base.height = H;
  const g = base.getContext("2d");
  g.fillStyle = "#EFE6DB"; g.fillRect(0, 0, W, H);
  g.fillStyle = "#1E130D"; g.font = '800 46px "Bricolage Grotesque", sans-serif'; g.fillText("Aisha Rahman", 60, 104);
  g.fillStyle = "#7A5E4E"; g.font = '500 22px "Onest", sans-serif'; g.fillText("BSc Computer Science  ·  Leeds", 60, 144);
  const spots = [];
  const rows = [[210, 0.9], [252, 0.7], [320, 0.82, "Python", 0], [362, 0.6], [430, 0.88, "SQL", 3], [472, 0.72], [540, 0.66, "stakeholder workshops", 2], [582, 0.8], [650, 0.5], [718, 0.84], [760, 0.62]];
  for (const [y, w, word, to] of rows){
    g.fillStyle = "rgba(60,36,24,.18)"; g.fillRect(60, y, (W - 120) * w, 14);
    if (word){
      g.font = '600 22px "Onest", sans-serif';
      const tw = g.measureText(word).width;
      g.fillStyle = "rgba(255,196,72,.8)"; g.fillRect(56, y - 22, tw + 10, 34);
      g.fillStyle = "#1E130D"; g.fillText(word, 61, y + 3);
      spots.push({u: (61 + tw) / W, v: (y - 4) / H, to});
    }
  }
  return {base, spots};
}

function pill(text){
  const c = document.createElement("canvas"), g = c.getContext("2d"), font = '600 44px "Onest", sans-serif';
  g.font = font;
  const w = Math.ceil(g.measureText(text).width) + 72, h = 84;
  c.width = w; c.height = h;
  g.font = font;
  g.fillStyle = "rgba(24,14,9,.92)"; g.strokeStyle = "rgba(255,160,100,.95)"; g.lineWidth = 3;
  g.beginPath(); g.roundRect(3, 3, w - 6, h - 6, (h - 6) / 2); g.fill(); g.stroke();
  g.fillStyle = "#FFC857"; g.beginPath(); g.arc(34, h / 2, 7, 0, Math.PI * 2); g.fill();
  g.fillStyle = "#FFEBD9"; g.textBaseline = "middle"; g.fillText(text, 52, h / 2 + 2);
  return c;
}
