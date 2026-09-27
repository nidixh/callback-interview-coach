// app-studio.js
// The app, inside the studio.
//
// The same room as the front door sits behind every screen, lit for what the screen is for: a warm rest light while you read your history, a spotlight for the interview, a low reading lamp for the debrief, and on the set-up screen a light that follows the level you pick.
// Film strips drift in the dark behind the quieter screens.
//
// The interviewer has two windows (faceports) drawn into the same canvas.
// On the set-up screen it waits in the corner and reads along as you type the advert.
// In the interview it sits on the stage: it speaks when a question arrives, listens while you answer, with a ring of sound around it showing your voice, and looks away to think while the answer is weighed.
//
// While you answer it pays attention the way a person would: it follows where you are in the camera frame, gives a small nod when you start speaking after a pause, and leans in when you stop to think.
// A ring round it shows the answer's length against the one to one and a half minute zone, and flashes when a filler word lands in the live captions.
// Behind Progress, every session's score stands in the room as a pillar of light.
//
// It reads the app and never writes to it: it wraps `show` and `render`, listens to the microphone meter and to what you type, and dresses what the app has drawn (score rings, the trend's fill, the clapperboard), so every feature behaves exactly as it did.
// While questions are being written and while the debrief is scored it stops drawing, so the graphics card is left to the language models and Whisper.

import * as THREE from "three";
import {createStage} from "./stage.js";
import {createHead} from "./head.js";

const $ = (id) => document.getElementById(id);

const canvas = document.createElement("canvas");
canvas.id = "studio"; canvas.setAttribute("aria-hidden", "true");
document.body.prepend(canvas);

// A sweep of warm light across the screen when the view changes, like film catching the light.
const leak = document.createElement("div");
leak.className = "leak"; leak.setAttribute("aria-hidden", "true");
document.body.appendChild(leak);

let stage = null;
try {
  stage = await createStage(canvas, {post: false, maxDpr: 1.25, fps: 30, dust: 650, bokeh: 20, mood: "rest"});
} catch (err) {
  document.body.classList.add("no3d");
}

// Dressing what the app draws Score rings, the trend's glow, the clapperboard: all added beside the app's own markup, redrawn whenever the app redraws it.
function scoreOf(el){ const m = /\d+/.exec(el.textContent || ""); return m ? Math.max(0, Math.min(100, +m[0])) : 0; }
function ringScores(){
  document.querySelectorAll("#recent .sess .sc, #answerHead .sc, #overallN").forEach(el => {
    el.style.setProperty("--v", scoreOf(el));
    el.classList.add("ringed");
  });
}
function fillTrend(){
  const svgEl = document.querySelector("#trend svg");
  if (!svgEl || svgEl.querySelector(".area")) return;
  const line = svgEl.querySelector("polyline.line:not(.eyes)");
  if (!line) return;
  const NS = "http://www.w3.org/2000/svg", pts = line.getAttribute("points").trim().split(/\s+/).map(p => p.split(",").map(Number));
  const base = 220 - 26;
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML = '<linearGradient id="trendFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#FF7A3D" stop-opacity=".38"/><stop offset="1" stop-color="#FF7A3D" stop-opacity="0"/></linearGradient>';
  const area = document.createElementNS(NS, "path");
  area.setAttribute("class", "area");
  area.setAttribute("d", `M${pts[0][0]},${base} ` + pts.map(([x, y]) => `L${x},${y}`).join(" ") + ` L${pts[pts.length - 1][0]},${base} Z`);
  area.setAttribute("fill", "url(#trendFill)");
  svgEl.prepend(defs); line.before(area);
}
function clapper(){
  const box = document.querySelector("#dismissed .center");
  if (!box || box.querySelector(".clap")) return;
  const c = document.createElement("div");
  c.className = "clap"; c.setAttribute("aria-hidden", "true");
  c.innerHTML = '<div class="arm"><i></i><i></i><i></i><i></i><i></i><i></i></div><div class="slate"><span>SCENE</span><b>CUT</b><span>TAKE</span></div>';
  box.prepend(c);
}
const watch = (id, fn) => { const el = $(id); if (el) new MutationObserver(fn).observe(el, {childList: true, subtree: true, characterData: true}); };
watch("recent", ringScores); watch("answerHead", ringScores); watch("overallN", ringScores); watch("trend", fillTrend);
ringScores(); fillTrend(); clapper();

if (stage){
  stage.rig.pos.set(0.6, 0.5, 7.5); stage.rig.look.set(0, 0.1, 0); stage.rig.fov = 36; stage.rig.ease = 1.1;
  requestAnimationFrame(() => canvas.classList.add("lit"));

  const strips = buildStrips(stage), pillars = buildPillars(stage);

  // The interviewer and its two windows
  const faceScene = new THREE.Scene(), faceCam = new THREE.PerspectiveCamera(28, 1, 0.1, 30);
  const head = createHead({shell: 700, neck: 120});
  faceScene.add(head.group);
  stage.onResize((w, h, dpr) => head.setPixelRatio(dpr));

  const card = $("stagecard"), veil = $("veil");
  const port = document.createElement("div");
  port.className = "faceport"; port.setAttribute("aria-hidden", "true");
  port.innerHTML = '<canvas class="voice"></canvas><i class="ring"></i><i class="ring b"></i><span class="af"><i></i><i></i><i></i><i></i><em></em></span><span class="tally"><i></i>REC</span><span class="tc" id="portTc">00:00:00</span><span class="fillers" id="portFill" hidden></span>';
  veil.after(port);
  const voice = port.querySelector("canvas.voice"), vg = voice.getContext("2d");

  const setupPane = document.querySelector("#setup .pane");
  const mini = document.createElement("div");
  mini.className = "faceport mini"; mini.setAttribute("aria-hidden", "true");
  mini.innerHTML = '<i class="ring"></i><span class="cap"><b>Your interviewer</b><em id="miniSay">Waiting for the advert</em></span>';
  setupPane.prepend(mini);
  const miniSay = $("miniSay");

  let view = "setup", state = "idle", lastPrompt = "", level = 0, reading = 0, levelName = "medium";
  // Attention: where you are on camera, when you last made a sound, a nod, a lean.
  let you = null, quietFor = 0, nod = 0, lean = 0, fillers = 0, fillPulse = 0;
  const live = () => view === "live" && veil.hidden;
  const inSetup = () => view === "setup" && innerWidth > 1180;
  const history = new Float32Array(120); let hi = 0;

  // What it does, each frame, in whichever window is showing.
  let shownLive = false, shownMini = false;
  stage.onFrame((dt, t) => {
    const isLive = live(), isMini = inSetup();
    if (isLive !== shownLive){ shownLive = isLive; port.classList.toggle("on", isLive); }
    if (isMini !== shownMini){ shownMini = isMini; mini.classList.toggle("on", isMini); }
    strips.update(dt, t, view); pillars.update(dt, t, view);
    if (!isLive && !isMini) return;
    if (isLive){
      // It meets your eye, with the small movements of somebody paying attention, and follows you if you move about in the frame.
      const wander = state === "thinking" ? [0.55, -0.2] : [Math.sin(t * 0.37) * 0.08, Math.sin(t * 0.53) * 0.05];
      const seen = you && performance.now() - you.at < 1500 && state !== "thinking";
      head.look(wander[0] + (seen ? (0.5 - you.x) * 1.1 : 0), wander[1] + (seen ? (0.42 - you.y) * 0.7 : 0));
      head.level = level;
      if (state === "recording"){
        if (level > 0.28){ if (quietFor > 0.7) nod = 1; quietFor = 0; } else quietFor += dt;
      } else quietFor = 0;
      lean += ((state === "recording" && quietFor > 1.3 ? 1 : 0) - lean) * (1 - Math.exp(-dt * 2.2));
    } else {
      // Reading: the eyes run along a line and drop to the next; idle, it glances about.
      reading = Math.max(0, reading - dt * 0.6);
      if (reading > 0){
        const line = (t * 0.55) % 1;
        head.look(-0.55 + line * 1.1, -0.35 - Math.floor((t * 0.55) % 4) * 0.06);
        head.mode = "thinking";
      } else {
        head.look(Math.sin(t * 0.31) * 0.25, Math.sin(t * 0.47) * 0.1);
        head.mode = "idle";
      }
    }
    head.update(dt, t);
    if (nod > 0.001){ head.group.rotation.x += Math.sin((1 - nod) * Math.PI * 2) * 0.09 * nod; nod = Math.max(0, nod - dt * 1.6); }
    fillPulse = Math.max(0, fillPulse - dt * 1.4);

    // The ring of sound: your voice while you answer, its voice while it asks.
    if (isLive){
      const v = state === "recording" ? level : head.uniforms.uMouth.value * 0.8;
      history[hi++ % history.length] = v;
      drawVoice(state === "recording");
    }
    level = Math.max(0, level - dt * 1.5);
  });

  // The last four seconds of sound, wrapped round the head: fine ticks for each moment, a smooth outline over them like a trace on a scope, and a bright head where the newest sound is written.
  let vw = 0, vh = 0;
  const ro = new ResizeObserver(() => { vw = voice.clientWidth; vh = voice.clientHeight; });
  ro.observe(voice);
  function drawVoice(yours){
    const d = Math.min(1.5, devicePixelRatio || 1);
    if (!vw) return;
    if (voice.width !== Math.round(vw * d)){ voice.width = vw * d; voice.height = vh * d; }
    vg.setTransform(d, 0, 0, d, 0, 0); vg.clearRect(0, 0, vw, vh);
    // An oval round the head, so the ring never crosses the face.
    const cx = vw / 2, cy = vh / 2, RX = Math.min(vw * 0.36, vh * 0.8), RY = vh * 0.36, n = history.length;
    const at = (a, k) => [cx + Math.cos(a) * RX * k, cy + Math.sin(a) * RY * k];
    const rgb = yours ? "169,184,255" : "255,170,110";
    vg.lineCap = "round"; vg.lineWidth = 1.5;
    const pts = [];
    for (let i = 0; i < n; i++){
      const v = history[(hi + i) % n], a = (i / n) * Math.PI * 2 - Math.PI / 2, k = 1 + 0.015 + v * 0.2;
      const [x0, y0] = at(a, 1), [x1, y1] = at(a, k);
      vg.strokeStyle = `rgba(${rgb},${0.1 + v * 0.55})`;
      vg.beginPath(); vg.moveTo(x0, y0); vg.lineTo(x1, y1); vg.stroke();
      pts.push(at(a, k + 0.03));
    }
    vg.beginPath();
    for (let i = 0; i <= n; i++){
      const p = pts[i % n], q = pts[(i + 1) % n], mx = (p[0] + q[0]) / 2, my = (p[1] + q[1]) / 2;
      if (i === 0) vg.moveTo(mx, my); else vg.quadraticCurveTo(p[0], p[1], mx, my);
    }
    vg.strokeStyle = `rgba(${rgb},.16)`; vg.lineWidth = 6; vg.stroke();
    vg.strokeStyle = `rgba(${rgb},.7)`; vg.lineWidth = 1.2; vg.stroke();
    const tip = pts[n - 1];
    vg.fillStyle = `rgba(${rgb},1)`; vg.shadowColor = `rgba(${rgb},.9)`; vg.shadowBlur = 12;
    vg.beginPath(); vg.arc(tip[0], tip[1], 2.6, 0, Math.PI * 2); vg.fill(); vg.shadowBlur = 0;
    // The length of the answer: a full turn is 2:15, the green arc is the one to one and a half minutes strong answers land in.
    if (yours){
      const K = 1.3, a0 = -Math.PI / 2, turn = Math.PI * 2 / 135;
      const oval = (from, to) => { vg.beginPath(); vg.ellipse(cx, cy, RX * K, RY * K, 0, from, to); vg.stroke(); };
      const secs = typeof startedAt !== "undefined" && startedAt ? (Date.now() - startedAt) / 1000 : 0;
      vg.lineCap = "butt"; vg.lineWidth = 3;
      vg.strokeStyle = "rgba(255,236,220,.07)"; oval(0, Math.PI * 2);
      vg.strokeStyle = "rgba(126,232,199,.45)"; oval(a0 + 60 * turn, a0 + 90 * turn);
      const inZone = secs >= 60 && secs <= 90, over = secs > 90;
      const col = over ? "255,200,87" : inZone ? "126,232,199" : "255,122,61";
      vg.strokeStyle = `rgba(${col},.95)`; vg.shadowColor = `rgba(${col},.8)`; vg.shadowBlur = 8;
      if (secs > 0.2) oval(a0, a0 + Math.min(135, secs) * turn);
      vg.shadowBlur = 0;
      if (fillPulse > 0.01){
        vg.strokeStyle = `rgba(255,107,133,${(fillPulse * 0.8).toFixed(3)})`; vg.lineWidth = 2 + fillPulse * 6;
        const k = 0.94 - (1 - fillPulse) * 0.06;
        vg.beginPath(); vg.ellipse(cx, cy, RX * k, RY * k, 0, 0, Math.PI * 2); vg.stroke();
      }
    }
  }

  function drawPort(renderer, el, fov){
    const r = el.getBoundingClientRect(), [W, H] = stage.size;
    if (r.width < 20 || r.height < 20 || r.bottom < 0 || r.top > H) return;
    faceCam.aspect = r.width / r.height;
    faceCam.fov = fov;
    faceCam.position.set(0, 0.02 - lean * 0.04, 3.85 - lean * 0.38); faceCam.lookAt(0, -0.04, 0);
    faceCam.updateProjectionMatrix();
    renderer.autoClear = false;
    renderer.setScissorTest(true);
    renderer.setScissor(r.left, H - r.bottom, r.width, r.height);
    renderer.setViewport(r.left, H - r.bottom, r.width, r.height);
    renderer.clearDepth();
    renderer.render(faceScene, faceCam);
    renderer.setScissorTest(false);
    renderer.setViewport(0, 0, W, H);
    renderer.autoClear = true;
  }
  stage.afterRender((renderer) => {
    if (live()) drawPort(renderer, port, port.clientWidth / port.clientHeight < 1.2 ? 34 : 28);
    else if (inSetup()) drawPort(renderer, mini, 30);
  });

  // Lighting the room for each screen
  const MOOD = {live: "stage", done: "read", sessions: "rest", progress: "rest", cvpage: "rest", calpage: "rest", dismissed: "night"};
  const SHOT = {
    live: {pos: [0, 0.2, 6.4], look: [0, 0.05, 0], fov: 34},
    done: {pos: [-1.6, 1.2, 8.8], look: [0, -0.4, 0], fov: 38},
    setup: {pos: [1.1, 0.55, 7.6], look: [0, 0.1, 0], fov: 36},
    sessions: {pos: [-2.4, 0.9, 7.8], look: [0, 0.3, -2], fov: 38},
    progress: {pos: [2.2, 1.6, 8.4], look: [0, 0.4, -2], fov: 38},
    cvpage: {pos: [-1.2, 0.3, 7.2], look: [0, 0.2, -1], fov: 36},
    calpage: {pos: [1.8, 0.2, 7.9], look: [0, 0.1, -2], fov: 38},
  };
  function light(){
    const shot = SHOT[view] || {pos: [-0.8, 0.8, 8.2], look: [0, 0, 0], fov: 36};
    stage.rig.pos.set(...shot.pos); stage.rig.look.set(...shot.look); stage.rig.fov = shot.fov;
    stage.setMood(view === "setup" ? levelName : (MOOD[view] || "rest"));
    // Scoring and question writing both run on the graphics card: stay dark for them.
    const busy = state === "preparing" || state === "analysing";
    stage.setActive(!busy);
    // The interviewer is the subject on the stage, so it moves at full rate except while your answer is transcribed live; a camera move always does.
    stage.setFps(view === "live" ? (state === "recording" ? 30 : 60) : view === "setup" ? 45 : 30);
  }

  // Reading the app
  const wrap = (name, after) => {
    const orig = window[name];
    if (typeof orig !== "function") return;
    window[name] = function(...args){ const out = orig.apply(this, args); try { after(...args); } catch (e) { /* the studio must never break the app */ } return out; };
  };
  wrap("show", (name) => {
    if (name !== view){ leak.classList.remove("go"); void leak.offsetWidth; leak.classList.add("go"); }
    view = name; light();
  });
  wrap("render", (s) => {
    if (!s) return;
    state = s.state;
    const text = s.prompt && s.prompt.text;
    if (text && text !== lastPrompt){
      lastPrompt = text;
      if (state === "waiting" || state === "recording") head.speak(text.split(/\s+/).length * 0.36 + 0.6);
    }
    if (state === "recording") head.hush();
    head.mode = state === "recording" ? "listening" : state === "thinking" ? "thinking" : "idle";
    card.dataset.state = state;
    light();
  });
  // Where you are in the camera frame, from the app's own face brackets.
  if (typeof placeBrackets === "function"){
    const placed = placeBrackets;
    window.placeBrackets = function(box, good){ const out = placed.apply(this, arguments); try { if (box) you = {x: box[0], y: box[1], at: performance.now()}; } catch (_) {} return out; };
  }
  // Filler words as they land in the live captions.
  const FILL = /\b(um+|uh+|erm+|er|like|basically|you know|sort of|kind of|actually)\b/gi;
  const portFill = $("portFill");
  function countFillers(s){
    if (s.state === "waiting"){ fillers = 0; portFill.hidden = true; return; }
    if (s.state !== "recording") return;
    const n = ((s.live_words || "").match(FILL) || []).length;
    if (n > fillers){ fillPulse = 1; portFill.hidden = false; portFill.textContent = n === 1 ? "1 filler" : n + " fillers"; portFill.classList.remove("hit"); void portFill.offsetWidth; portFill.classList.add("hit"); }
    fillers = n;
  }
  // The question arrives a word at a time, as it is spoken.
  const qEl = $("question");
  let qShown = "";
  function wordIn(){
    const text = qEl.textContent;
    if (!text || text === qShown || qEl.querySelector(".qw")) return;
    qShown = text;
    qEl.setAttribute("aria-label", text);
    qEl.innerHTML = text.split(/(\s+)/).map((w, i) => /^\s+$/.test(w) ? w : `<span class="qw" aria-hidden="true" style="--i:${i / 2}">${w.replace(/[&<>]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]))}</span>`).join("");
  }
  wrap("render", (s) => { if (!s) return; document.body.dataset.state = s.state || ""; countFillers(s); if (s.state === "waiting" || s.state === "recording") wordIn(); });

  // The microphone meter, while an answer is being recorded.
  if (typeof field !== "undefined" && field && typeof field.excite === "function"){
    const excite = field.excite;
    field.excite = (v) => { excite(v); if (typeof v === "number") level = Math.max(level, Math.min(1, (v - 0.3) / 0.7)); };
  }

  // Set-up: it reads as you type, and the room takes the colour of the level.
  const jd = $("jd");
  let sayTimer = 0;
  const say = (text, ms = 2600) => { miniSay.textContent = text; clearTimeout(sayTimer); sayTimer = setTimeout(() => { miniSay.textContent = jd && jd.value.trim() ? "Ready when you are" : "Waiting for the advert"; }, ms); };
  if (jd) jd.addEventListener("input", () => { reading = 1.6; miniSay.textContent = "Reading the advert"; clearTimeout(sayTimer); sayTimer = setTimeout(() => say("Ready when you are", 1), 1800); });
  const seg = $("lvlSeg");
  function readLevel(){
    const on = seg && seg.querySelector('[aria-checked="true"]');
    const next = (on && on.dataset.level) || "medium";
    if (next !== levelName){ levelName = next; if (view === "setup"){ light(); say({easy: "An easy one, then", medium: "A fair test", hard: "Let's make it hard"}[next] || ""); } }
  }
  if (seg){ new MutationObserver(readLevel).observe(seg, {attributes: true, subtree: true, attributeFilter: ["aria-checked", "class"]}); readLevel(); }
  const example = $("example");
  if (example) example.addEventListener("click", () => { head.absorb(); head.speak(1.1); reading = 2.2; say("A new job. Let me read it"); });

  const on = document.querySelector(".view.on");
  if (on) view = on.id;
  light();

  // A small clock in the corner of the faceport, as on a camera.
  const tcEl = $("portTc"), started = performance.now();
  setInterval(() => {
    if (!live()) return;
    const s = Math.floor((performance.now() - started) / 1000);
    tcEl.textContent = [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(n => String(n).padStart(2, "0")).join(":");
  }, 1000);
}

// Pillars Behind Progress, each finished interview stands as a column of light in the room, as tall as its score, rising in order when the screen opens.
function buildPillars(stage){
  const MAX = 24, geo = new THREE.BoxGeometry(1, 1, 1);
  geo.translate(0, 0.5, 0);
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: {uTime: {value: 0}, uAmt: {value: 0}},
    vertexShader: /* glsl */`
      attribute vec3 aCol; attribute float aRise; varying vec3 vCol; varying float vY; varying float vRise;
      void main(){ vCol = aCol; vY = position.y; vRise = aRise; gl_Position = projectionMatrix * modelViewMatrix * instanceMatrix * vec4(position, 1.0); }`,
    fragmentShader: /* glsl */`
      uniform float uTime, uAmt; varying vec3 vCol; varying float vY; varying float vRise;
      void main(){
        float cap = smoothstep(0.9, 1.0, vY) * 1.6;
        float scan = 0.5 + 0.5 * sin(vY * 40.0 - uTime * 2.0);
        float a = (0.1 + vY * 0.25 + cap + scan * 0.05) * uAmt * vRise;
        gl_FragColor = vec4(vCol, a * 0.55);
        #include <colorspace_fragment>
      }`,
  });
  const bars = new THREE.InstancedMesh(geo, mat, MAX);
  const col = new Float32Array(MAX * 3), rise = new Float32Array(MAX);
  geo.setAttribute("aCol", new THREE.InstancedBufferAttribute(col, 3));
  geo.setAttribute("aRise", new THREE.InstancedBufferAttribute(rise, 1));
  bars.count = 0; bars.frustumCulled = false; bars.renderOrder = 7;
  stage.scene.add(bars);
  let scores = [], born = 0;
  const dummy = new THREE.Object3D(), c = new THREE.Color();
  window.addEventListener("callback:trend", (e) => { scores = (e.detail || []).slice(-MAX); born = performance.now(); });
  return {
    update(dt, t, view){
      mat.uniforms.uTime.value = t;
      mat.uniforms.uAmt.value += ((view === "progress" && scores.length ? 1 : 0) - mat.uniforms.uAmt.value) * (1 - Math.exp(-dt * 2));
      bars.visible = mat.uniforms.uAmt.value > 0.01;
      if (!bars.visible) return;
      const n = scores.length, age = (performance.now() - born) / 1000;
      bars.count = n;
      for (let i = 0; i < n; i++){
        const k = Math.min(1, Math.max(0, (age - i * 0.08) / 1.1)), e = 1 - Math.pow(1 - k, 3);
        const h = Math.max(0.05, scores[i] / 100 * 3.2) * e;
        dummy.position.set(-3.4 + (n > 1 ? i / (n - 1) : 0.5) * 6.8, -2.3, -3.6 - Math.sin(i / Math.max(1, n - 1) * Math.PI) * 1.2);
        dummy.scale.set(0.16, h, 0.16); dummy.updateMatrix(); bars.setMatrixAt(i, dummy.matrix);
        c.set(scores[i] >= 70 ? "#7EE8C7" : scores[i] >= 45 ? "#FF9A5E" : "#FF6B85");
        col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b; rise[i] = e;
      }
      bars.instanceMatrix.needsUpdate = true; geo.attributes.aCol.needsUpdate = true; geo.attributes.aRise.needsUpdate = true;
    },
  };
}

// Film strips Lengths of film drifting in the dark behind the quieter screens, their frames lit faintly, running slowly through the gate.
function buildStrips(stage){
  const c = document.createElement("canvas"); c.width = 1024; c.height = 96;
  const g = c.getContext("2d");
  g.fillStyle = "#120b08"; g.fillRect(0, 0, 1024, 96);
  for (let x = 6; x < 1024; x += 22){ g.fillStyle = "#2a1c15"; g.beginPath(); g.roundRect(x, 6, 12, 10, 2); g.roundRect(x, 80, 12, 10, 2); g.fill(); }
  for (let x = 4; x < 1024; x += 128){
    const grad = g.createLinearGradient(x, 22, x + 120, 74);
    grad.addColorStop(0, "rgba(255,150,90,.55)"); grad.addColorStop(1, "rgba(120,50,30,.25)");
    g.fillStyle = grad; g.fillRect(x, 22, 120, 52);
    g.strokeStyle = "rgba(255,200,160,.25)"; g.lineWidth = 2; g.strokeRect(x, 22, 120, 52);
  }
  const tex = new THREE.CanvasTexture(c); tex.colorSpace = THREE.SRGBColorSpace; tex.wrapS = THREE.RepeatWrapping;
  const mat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, side: THREE.DoubleSide, blending: THREE.AdditiveBlending,
    uniforms: {uTex: {value: tex}, uTime: {value: 0}, uAmt: {value: 0}},
    vertexShader: /* glsl */`
      uniform float uTime; attribute float aSeed; varying vec2 vUv; varying float vEdge;
      void main(){
        vUv = uv; vec3 p = position;
        p.y += sin(p.x * 0.45 + uTime * 0.25 + aSeed) * 0.45;
        p.z += cos(p.x * 0.33 + uTime * 0.2 + aSeed * 2.0) * 0.7;
        vEdge = smoothstep(0.0, 0.12, uv.x) * smoothstep(1.0, 0.88, uv.x);
        gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
      }`,
    fragmentShader: /* glsl */`
      uniform sampler2D uTex; uniform float uTime, uAmt; varying vec2 vUv; varying float vEdge;
      void main(){
        vec3 c = texture2D(uTex, vec2(vUv.x * 3.0 - uTime * 0.02, vUv.y)).rgb;
        gl_FragColor = vec4(c, vEdge * uAmt * 0.26);
        #include <colorspace_fragment>
      }`,
  });
  const group = new THREE.Group();
  [[-1.2, 2.6, -8.5, 0.25, 0.1], [0.8, -1.2, -9.5, -0.18, 2.7], [2.6, 3.8, -11, 0.08, 4.1]].forEach(([x, y, z, rot, seed]) => {
    const geo = new THREE.PlaneGeometry(14, 0.75, 160, 1);
    geo.setAttribute("aSeed", new THREE.BufferAttribute(new Float32Array(geo.attributes.position.count).fill(seed), 1));
    const m = new THREE.Mesh(geo, mat);
    m.position.set(x, y, z); m.rotation.z = rot; m.rotation.y = 0.25 * Math.sign(rot);
    group.add(m);
  });
  stage.scene.add(group);
  const WANT = {sessions: 1, progress: 1, cvpage: 0.8, calpage: 0.9, setup: 0.55, done: 0.35, live: 0.12, dismissed: 0.5};
  return {
    update(dt, t, view){
      mat.uniforms.uTime.value = t;
      mat.uniforms.uAmt.value += ((WANT[view] ?? 0.5) - mat.uniforms.uAmt.value) * (1 - Math.exp(-dt * 1.5));
    },
  };
}
