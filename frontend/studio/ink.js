// ink.js
// The screens, framed, with notes in the margin.
//
// Each screen sits in a frame drawn to its measured size, and carries its notes as data: circles, arrows and underlines in image pixels, and jotted notes.
// Scrolling a screen into view draws the frame, brings the screen into focus, then draws the pen marks and writes the notes in order.
// Hovering a point in the text lights its marks and dims the rest.
"use strict";
(() => {
  const NS = "http://www.w3.org/2000/svg";
  const PEN = {ember: "#FF7A3D", gold: "#FFC857", rose: "#FF6B85", mint: "#7EE8C7"};
  const node = (name, attrs) => { const n = document.createElementNS(NS, name); for (const k in attrs) n.setAttribute(k, attrs[k]); return n; };
  let seed = 11;
  const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  const f = v => v.toFixed(1);
  function smooth(pts){
    let d = `M${f(pts[0][0])},${f(pts[0][1])}`;
    for (let i = 1; i < pts.length - 1; i++) d += ` Q${f(pts[i][0])},${f(pts[i][1])} ${f((pts[i][0] + pts[i + 1][0]) / 2)},${f((pts[i][1] + pts[i + 1][1]) / 2)}`;
    const last = pts[pts.length - 1]; return d + ` L${f(last[0])},${f(last[1])}`;
  }
  // A hand-drawn loop: a little more than one turn, wobbling, never quite closed.
  function loop(cx, cy, rx, ry){
    const pts = [], start = -2.2 + rnd() * .6, turn = Math.PI * 2 * 1.1;
    for (let i = 0; i <= 44; i++){ const t = start + turn * i / 44, w = 1 + (rnd() - .5) * .05 + i / 44 * .06; pts.push([cx + Math.cos(t) * rx * w, cy + Math.sin(t) * ry * w]); }
    return smooth(pts);
  }
  function wavy(x1, y1, x2, y2){
    const pts = []; for (let i = 0; i <= 16; i++){ const t = i / 16; pts.push([x1 + (x2 - x1) * t, y1 + (y2 - y1) * t + Math.sin(t * Math.PI * 4) * 4 + (rnd() - .5) * 2]); }
    return smooth(pts);
  }
  function arrow(x1, y1, x2, y2, bend){
    const dx = x2 - x1, dy = y2 - y1, len = Math.hypot(dx, dy);
    const cx = (x1 + x2) / 2 - dy * bend, cy = (y1 + y2) / 2 + dx * bend;
    const a = Math.atan2(y2 - cy, x2 - cx), h = Math.min(30, len * .38);
    return [`M${x1},${y1} Q${f(cx)},${f(cy)} ${x2},${y2}`,
            `M${f(x2 - h * Math.cos(a - .5))},${f(y2 - h * Math.sin(a - .5))} L${x2},${y2} L${f(x2 - h * Math.cos(a + .5))},${f(y2 - h * Math.sin(a + .5))}`];
  }
  function ink(fig){
    const screen = fig.querySelector(".screen"), img = screen.querySelector("img");
    const W = +img.getAttribute("width"), H = +img.getAttribute("height") + (+fig.dataset.gutter || 0);
    const svg = node("svg", {class: "ink", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", "aria-hidden": "true"});
    let d = 1.3;
    const items = JSON.parse(screen.querySelector(".inkdata").textContent);
    for (const it of items){
      const colour = PEN[it.pen] || PEN.ember;
      const paths = it.circle ? [loop(...it.circle)] : it.under ? [wavy(...it.under)] : it.arrow ? arrow(...it.arrow) : [];
      paths.forEach((pd, i) => {
        const p = node("path", {d: pd, pathLength: 1, stroke: colour, "data-k": it.k, class: i ? "head" : ""});
        p.style.setProperty("--d", (d + i * .4) + "s"); svg.appendChild(p);
      });
      if (it.jot){
        const j = document.createElement("span"); j.className = "jot"; j.dataset.k = it.k; j.setAttribute("aria-hidden", "true");
        const [line, sub] = it.text.split("|");
        j.textContent = line; if (sub){ const s = document.createElement("small"); s.textContent = sub; j.appendChild(s); }
        j.style.left = (it.jot[0] / W * 100) + "%"; j.style.top = (it.jot[1] / H * 100) + "%";
        j.style.setProperty("--r", (it.rot || -3) + "deg"); j.style.setProperty("--c", colour); j.style.setProperty("--d", d + "s");
        screen.appendChild(j);
      }
      d += it.jot ? .34 : .22;
    }
    screen.appendChild(svg);
  }
  // The frame, drawn to the figure's size: a folder tab, cut corners, a doubled edge on one cut, ticks under the frame and a leader line out to the text.
  function frame(fig){
    const svg = fig.querySelector(".frame"), W = fig.clientWidth, H = fig.clientHeight;
    if (!W) return;
    const T = 24, tab = fig.querySelector(".tab"), tw = tab.offsetWidth + 10, x0 = 24;
    const flip = fig.closest(".stop").classList.contains("flip");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.innerHTML = "";
    svg.appendChild(node("path", {class: "tabshape", d: `M${x0},${T} L${x0 + T},0 L${x0 + T + tw},0 L${x0 + 2 * T + tw},${T} Z`}));
    svg.appendChild(node("path", {class: "body", pathLength: 1, d:
      `M0,${T + 16} L16,${T} L${W - 30},${T} L${W},${T + 30} L${W},${H - 12} L${W - 12},${H} L40,${H} L0,${H - 40} Z`}));
    let acc = `M${W - 22},${T + 5} L${W - 5},${T + 22} M18,${H - 34} L34,${H - 18}`;
    for (let x = W - 180; x <= W - 30; x += 8) acc += ` M${x},${H + 7} L${x},${H + (x % 40 === 0 ? 15 : 11)}`;
    acc += ` M${W - 18},${H - 30} L${W - 18},${H - 18} L${W - 30},${H - 18}`;
    svg.appendChild(node("path", {class: "acc", d: acc}));
    // Nothing beside it to lead to
    if (fig.closest(".stop").classList.contains("wide")) return;
    const y = Math.round(H * .44), out = flip ? -46 : W + 46, edge = flip ? 0 : W;
    svg.appendChild(node("path", {class: "lead", pathLength: 1, d: `M${edge},${y} L${(edge + out) / 2},${y} L${out},${y}`}));
    svg.appendChild(node("circle", {class: "node", cx: out, cy: y, r: 4}));
  }
  const figs = [...document.querySelectorAll(".hud")];
  figs.forEach(fig => {
    ink(fig); frame(fig);
    const toggle = fig.querySelector(".inktoggle");
    toggle.onclick = () => { const on = fig.classList.toggle("bare"); toggle.setAttribute("aria-pressed", on ? "false" : "true"); toggle.textContent = on ? "notes off" : "notes on"; };
    const say = fig.closest(".stop").querySelector(".say");
    say.querySelectorAll("li[data-k]").forEach(li => {
      const light = on => {
        fig.classList.toggle("focus", on); li.classList.toggle("hot", on);
        fig.querySelectorAll(`[data-k="${li.dataset.k}"]`).forEach(m => m.classList.toggle("hot", on));
      };
      li.addEventListener("mouseenter", () => light(true)); li.addEventListener("mouseleave", () => light(false));
    });
  });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => figs.forEach(frame));
  const resized = new ResizeObserver(es => es.forEach(e => frame(e.target)));
  figs.forEach(fig => resized.observe(fig));
  const seen = new IntersectionObserver(es => es.forEach(e => { if (e.isIntersecting){ e.target.classList.add("in"); seen.unobserve(e.target); } }), {threshold: .28});
  document.querySelectorAll(".stop").forEach(st => seen.observe(st));
})();
