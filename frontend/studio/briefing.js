// briefing.js
// Set up: the briefing room.
//
// As an advert is pasted or typed, a board under it pulls out what the advert stresses: the skills it names, how it wants you to work, and the places an interviewer is likely to press (years, a degree, leading, on-call, weekends).
// Each new phrase flies out of the editor onto the board.
// With a CV present, every phrase is checked against it: on your CV, or not, which is where a question will probe.
// A dial shows how demanding the advert measured, read from the app's own measurement.
// It is a reading of the advert on screen, not the question plan, and says so.

const $ = (id) => document.getElementById(id);
const esc = (t) => String(t == null ? "" : t).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));

const SKILLS = ["Python", "SQL", "Excel", "Java", "JavaScript", "TypeScript", "React", "Node", "AWS", "Azure", "GCP", "Docker", "Kubernetes", "Git", "Linux",
  "Power BI", "Tableau", "C#", "C++", ".NET", "HTML", "CSS", "REST", "APIs", "machine learning", "statistics", "data analysis", "data modelling", "ETL",
  "Agile", "Scrum", "CRM", "Salesforce", "SAP", "food hygiene", "food safety", "customer service", "cash handling", "stock control", "stock", "inventory",
  "forklift", "first aid", "health and safety", "cleaning", "driving licence", "Microsoft Office", "Microsoft Word", "PowerPoint", "budgeting", "forecasting",
  "accounting", "bookkeeping", "marketing", "SEO", "social media", "copywriting", "Figma", "Photoshop", "project management", "testing", "CI/CD",
  "security", "networking", "cloud", "Spark", "Airflow", "dbt", "Terraform", "Golang", "Rust", "Swift", "Kotlin", "PHP", "Django", "Flask", "pandas",
  "reporting", "dashboards", "documentation", "scheduling", "retail", "sales", "hospitality", "kitchen", "dishwashing", "warehouse", "logistics",
  "safeguarding", "teaching", "research", "writing", "editing", "design", "UX", "Jira", "Confluence", "microservices", "distributed systems",
  "system design", "incident response", "monitoring", "observability", "performance", "architecture", "code review", "mentoring"];
const TRAITS = [["communication", /communicat\w*/i], ["teamwork", /team ?work|team player|collaborat\w*/i], ["leadership", /\blead(ership|ing)\b/i],
  ["initiative", /initiative|self[- ]motivated|proactive/i], ["problem solving", /problem[- ]solv\w*/i], ["attention to detail", /attention to detail|detail[- ]oriented/i],
  ["organisation", /organi[sz]ed|organi[sz]ation(al)? skills/i], ["time management", /time management|prioriti[sz]\w*/i], ["adaptability", /adaptab\w*|flexib\w*/i],
  ["reliability", /reliab\w*|punctual\w*|dependab\w*/i], ["working alone", /independent\w*|on your own|autonomous\w*/i], ["under pressure", /under pressure|fast[- ]paced|busy/i],
  ["curiosity", /curio\w*|eager to learn|keen to learn/i], ["ownership", /\bown(ership)?\b|accountab\w*/i], ["customer focus", /customer[- ]focus\w*|customer[- ]first/i]];
const STAKES = [[/(\d+)\+?\s*(?:or more\s*)?years?/i, (m) => `${m[1]}+ years' experience`], [/\bdegree\b/i, () => "A degree"], [/\bstakeholders?\b|\bclients?\b|\btraders\b|\bnon[- ]technical\b|\brisk managers\b/i, () => "Explaining to others"],
  [/\bon[- ]call\b/i, () => "On-call"], [/\bweekends?\b/i, () => "Weekends"], [/\bshifts?\b/i, () => "Shifts"], [/\bnights?\b/i, () => "Nights"],
  [/\btravel\b/i, () => "Travel"], [/\bdeadlines?\b/i, () => "Deadlines"], [/\bbudgets?\b/i, () => "Budgets"], [/\bsenior\b/i, () => "Seniority"],
  [/\bcertif\w*/i, () => "A certification"], [/\bclearance\b/i, () => "Security clearance"], [/\bmanag(e|ing) (a )?team\b/i, () => "Managing a team"],
  [/\bmentor\w*/i, () => "Mentoring others"], [/\bproduction\b/i, () => "Production systems"], [/\bscale|scalab\w*/i, () => "Scale"]];

// The board
const pane = document.querySelector("#setup .pane"), editor = pane.querySelector(".editor");
const board = document.createElement("section");
board.className = "brief"; board.setAttribute("aria-label", "What stands out in the advert");
board.innerHTML = `
  <header>
    <div><h2>What stands out in the advert</h2><p class="fore" id="briefFore"></p></div>
    <svg class="dial" id="briefDial" viewBox="0 0 120 82" role="img" aria-label="How demanding the advert measured">
      <path class="arc" d="M12 62 A48 48 0 0 1 108 62"/>
      <path class="arc z1" d="M12 62 A48 48 0 0 1 36.2 20.4"/><path class="arc z2" d="M36.2 20.4 A48 48 0 0 1 83.8 20.4"/><path class="arc z3" d="M83.8 20.4 A48 48 0 0 1 108 62"/>
      <g class="ticks"></g>
      <g class="needle" id="briefNeedle"><path d="M60 62 L58 30 L60 18 L62 30 Z"/><circle cx="60" cy="62" r="4.5"/></g>
      <text x="60" y="80" class="val" id="briefVal">-</text>
    </svg>
  </header>
  <div class="cols">
    <div class="col" data-k="skill"><h3>Skills it names</h3><div class="chips"></div></div>
    <div class="col" data-k="trait"><h3>How it wants you to work</h3><div class="chips"></div></div>
    <div class="col" data-k="stake"><h3>Where you will be pressed</h3><div class="chips"></div></div>
  </div>
  <p class="key" id="briefKey" hidden><span class="y"><i></i>On your CV</span><span class="n"><i></i>Not on your CV, so expect a question on it</span></p>
  <p class="empty" id="briefEmpty">Paste an advert and what it asks for lands here, checked against your CV.</p>
  <p class="fine">Read from the advert on this screen. The questions themselves are planned when you start.</p>`;
editor.after(board);
const ticks = board.querySelector(".ticks");
for (let i = 0; i <= 20; i++){
  const a = Math.PI * (1 - i / 20), r1 = i % 5 ? 41 : 38, r2 = 45;
  ticks.insertAdjacentHTML("beforeend", `<line x1="${60 + Math.cos(a) * r1}" y1="${62 - Math.sin(a) * r1}" x2="${60 + Math.cos(a) * r2}" y2="${62 - Math.sin(a) * r2}"/>`);
}

function extract(text){
  const out = {skill: new Map(), trait: new Map(), stake: new Map()};
  if (!text.trim()) return out;
  for (const s of SKILLS){
    const re = new RegExp(`(^|[^\\w+#.])${s.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}(?![\\w+#])`, s.length <= 3 ? "g" : "gi");
    const n = (text.match(re) || []).length;
    if (n) out.skill.set(s, n);
  }
  // Named tools and methods the list does not know: short capitalised terms (SABR, SOFR, PDE), and a few two-word names written with capitals.
  const COMMON = /^(UK|US|EU|CV|IT|HR|OR|AND|THE|TO|OF|IN|AN|AM|PM|BA|BSC|MSC|PHD|GCSE|NVQ|FTE|WFH|ASAP|ETC|EG|IE|NB|CEO|CTO|CFO|COO|VP|LTD|PLC|INC|LLC|API|APIS)$/;
  for (const m of text.matchAll(/\b[A-Z][A-Z0-9+#&]{1,6}\b/g)){
    const t = m[0];
    if (COMMON.test(t) || [...out.skill.keys()].some(k => k.toUpperCase() === t)) continue;
    out.skill.set(t, (out.skill.get(t) || 0) + 1);
  }
  for (const m of text.matchAll(/\b(Monte Carlo|Black[- ]Scholes|Power Apps|Google Analytics|Adobe [A-Z][a-z]+|Microsoft [A-Z][a-z]+)\b/g)) out.skill.set(m[1], (out.skill.get(m[1]) || 0) + 1);
  // A short name inside a longer one ("stock" in "stock control") is the longer one's.
  for (const s of [...out.skill.keys()]) for (const t of out.skill.keys()) if (t !== s && t.toLowerCase().includes(s.toLowerCase())) out.skill.delete(s);
  for (const [name, re] of TRAITS){ const n = (text.match(new RegExp(re.source, "gi")) || []).length; if (n) out.trait.set(name, n); }
  for (const [re, label] of STAKES){ const m = re.exec(text); if (m) out.stake.set(label(m), 1); }
  return out;
}

const cvText = () => ((typeof exampleCv !== "undefined" && exampleCv && exampleCv.text) || ($("cv") && $("cv").value) || "").toLowerCase();
const STAKE_WORDS = {"A degree": /degree|bsc|ba |msc|university/, "Explaining to others": /stakeholder|client|customer|present|explain|non[- ]technical/, "On-call": /on[- ]call|support rota/, "Weekends": /weekend/,
  "Shifts": /shift/, "Nights": /night/, "Travel": /travel/, "Deadlines": /deadline|deliver/, "Budgets": /budget/, "Seniority": /senior|lead/, "A certification": /certif/,
  "Security clearance": /clearance/, "Managing a team": /manag|led a team|team lead/, "Mentoring others": /mentor|coach|train/, "Production systems": /production|live system/, "Scale": /scale|million|thousand/};
function onCv(kind, name, cv){
  if (!cv) return null;
  if (kind === "stake"){
    const years = /^(\d+)\+ years/.exec(name);
    if (years){ const got = [...cv.matchAll(/(\d+)\+?\s*years?/g)].map(m => +m[1]); return got.some(n => n >= +years[1]); }
    return STAKE_WORDS[name] ? STAKE_WORDS[name].test(cv) : null;
  }
  if (kind === "trait") return (TRAITS.find(t => t[0] === name) || [0, /$^/])[1].test(cv);
  return cv.includes(name.toLowerCase());
}

let lastText = null, lastCv = null;
function update(){
  const text = $("jd").value, cv = cvText();
  if (text === lastText && cv === lastCv) return;
  const fresh = lastText !== null;
  lastText = text; lastCv = cv;
  const found = extract(text), from = editor.getBoundingClientRect();
  let any = false, flew = 0;
  for (const col of board.querySelectorAll(".col")){
    const k = col.dataset.k, host = col.querySelector(".chips"), want = found[k];
    const have = new Map([...host.children].map(c => [c.dataset.name, c]));
    for (const [name, el] of have) if (!want.has(name)){ el.classList.add("gone"); setTimeout(() => el.remove(), 260); }
    const entries = [...want.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10);
    entries.forEach(([name, n], rank) => {
      any = true;
      let el = have.get(name);
      const hit = onCv(k, name, cv);
      if (!el){
        el = document.createElement("span");
        el.className = "chip"; el.dataset.name = name;
        el.innerHTML = `<i></i>${esc(name)}${n > 1 ? `<small>&times;${n}</small>` : ""}`;
        host.appendChild(el);
        if (fresh){
          const to = el.getBoundingClientRect();
          const dx = from.left + from.width * (0.3 + Math.random() * 0.4) - to.left, dy = from.top + from.height * (0.25 + Math.random() * 0.5) - to.top;
          el.animate([{transform: `translate(${dx}px,${dy}px) scale(.5)`, opacity: 0, filter: "blur(6px)"}, {transform: "none", opacity: 1, filter: "blur(0)"}],
            {duration: 900, delay: Math.min(flew++, 14) * 55, easing: "cubic-bezier(.16,1,.3,1)", fill: "backwards"});
        }
      } else {
        const sm = el.querySelector("small");
        if (n > 1){ if (sm) sm.innerHTML = `&times;${n}`; else el.insertAdjacentHTML("beforeend", `<small>&times;${n}</small>`); } else if (sm) sm.remove();
      }
      el.style.order = rank;
      el.classList.toggle("y", hit === true); el.classList.toggle("n", hit === false);
      el.style.setProperty("--w", Math.min(1, 0.55 + n * 0.15).toFixed(2));
    });
    col.classList.toggle("none", !entries.length);
  }
  $("briefEmpty").hidden = any;
  $("briefKey").hidden = !any || !cv;
  board.classList.toggle("on", any);
}

// The dial
let needle = -90, needleV = 0, needleWant = -90, dialRaf = 0;
function readLevel(){
  const el = $("jdLevel"), count = document.querySelector("#countSeg .on");
  const n = count ? +count.dataset.n : 5;
  $("briefFore").textContent = `Up to ${n} questions, about ${Math.round(n * 1.3)} minutes.`;
  if (!el || el.hidden){ needleWant = -90; $("briefVal").textContent = "-"; board.dataset.level = ""; }
  else {
    const m = /(\d+)\s*$/.exec(el.textContent.trim()), v = m ? +m[1] : 50;
    needleWant = -90 + Math.max(0, Math.min(100, v)) * 1.8;
    $("briefVal").textContent = v;
    board.dataset.level = el.dataset.level || "";
  }
  if (!dialRaf) dialRaf = requestAnimationFrame(spring);
}
function spring(){
  // A needle on a spring: it overshoots and settles, like a real meter.
  needleV += (needleWant - needle) * 0.09; needleV *= 0.78; needle += needleV;
  $("briefNeedle").setAttribute("transform", `rotate(${needle.toFixed(2)} 60 62)`);
  dialRaf = Math.abs(needleWant - needle) > 0.05 || Math.abs(needleV) > 0.05 ? requestAnimationFrame(spring) : 0;
}
new MutationObserver(readLevel).observe($("jdLevel"), {attributes: true, childList: true, subtree: true, characterData: true});
new MutationObserver(readLevel).observe($("countSeg"), {attributes: true, subtree: true, attributeFilter: ["class"]});
readLevel();

// The advert can change by typing, pasting, Clear or an example being dealt, so it is simply looked at a few times a second while this screen is up.
$("jd").addEventListener("input", () => { clearTimeout(update.t); update.t = setTimeout(update, 280); });
setInterval(() => { if (typeof current === "undefined" || current === "setup") update(); }, 700);
update();
