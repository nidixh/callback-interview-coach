// stage.js
// The studio: one dark room that both pages sit in.
//
// A backdrop of warm haze, two beams of light cutting through it, dust that only shows where a beam catches it, and a floor with an actor's mark taped to it.
// The camera eases toward wherever it is sent (rig), and drifts a little with the cursor.
//
// It asks the browser for the power-saving graphics, starts at a modest resolution, and steps the resolution (and then the glow) down by itself if frames start to take too long, and back up when there is room.
// It stops drawing when the tab is hidden or when told to (setActive), so it never competes with the speech models while they work.

import * as THREE from "three";

const NOISE = /* glsl */`
  float hash(vec3 p){ p = fract(p * 0.3183099 + 0.1); p *= 17.0; return fract(p.x * p.y * p.z * (p.x + p.y + p.z)); }
  float noise(vec3 x){
    vec3 i = floor(x), f = fract(x); f = f * f * (3.0 - 2.0 * f);
    return mix(mix(mix(hash(i), hash(i + vec3(1,0,0)), f.x), mix(hash(i + vec3(0,1,0)), hash(i + vec3(1,1,0)), f.x), f.y),
               mix(mix(hash(i + vec3(0,0,1)), hash(i + vec3(1,0,1)), f.x), mix(hash(i + vec3(0,1,1)), hash(i + vec3(1,1,1)), f.x), f.y), f.z);
  }`;

export const MOODS = {
  // key: the beam's colour; glow: the haze behind the head; beam, haze: strengths.
  studio: {key: "#FFB27A", glow: "#FF7A3D", rim: "#A9B8FF", beam: 1.0, haze: 1.0, rimBeam: 0.55, floor: 1.0},
  stage:  {key: "#FFC08F", glow: "#FF6A2E", rim: "#A9B8FF", beam: 1.25, haze: 1.2, rimBeam: 0.8, floor: 1.2},
  rest:   {key: "#FFB27A", glow: "#C8582A", rim: "#8F9FE8", beam: 0.7, haze: 0.8, rimBeam: 0.35, floor: 0.8},
  read:   {key: "#FFD9B8", glow: "#7A3A22", rim: "#A9B8FF", beam: 0.45, haze: 0.55, rimBeam: 0.25, floor: 0.6},
  night:  {key: "#FFB27A", glow: "#5A2A18", rim: "#A9B8FF", beam: 0.3, haze: 0.5, rimBeam: 0.2, floor: 0.4},
  // The three levels, as lighting: a soft warm lamp, a brighter gold, a hard red key with a cold rim.
  easy:   {key: "#FFC9A0", glow: "#B8672F", rim: "#9FB0F0", beam: 0.65, haze: 0.75, rimBeam: 0.3, floor: 0.75},
  medium: {key: "#FFD27A", glow: "#D08A1E", rim: "#A9B8FF", beam: 0.95, haze: 0.95, rimBeam: 0.45, floor: 0.95},
  hard:   {key: "#FF6A4A", glow: "#B0201A", rim: "#7F95FF", beam: 1.25, haze: 1.15, rimBeam: 0.95, floor: 1.1},
};

export async function createStage(canvas, opts = {}){
  // manual: the page calls render(now) itself once per frame, after it has placed the camera, so the room and the page never drift a frame apart.
  const o = Object.assign({post: false, maxDpr: 1.5, fps: 60, dust: 1400, bokeh: 34, mood: "studio", manual: false}, opts);
  const renderer = new THREE.WebGLRenderer({canvas, antialias: !o.post, alpha: false, powerPreference: "low-power", stencil: false});
  renderer.setClearColor(0x0b0706, 1);
  renderer.toneMapping = THREE.NoToneMapping;
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(34, 1, 0.1, 120);
  camera.position.set(0, 0.4, 6);

  const mood = {}, want = {};
  for (const [k, v] of Object.entries(MOODS[o.mood])) { mood[k] = typeof v === "string" ? new THREE.Color(v) : v; want[k] = mood[k] instanceof THREE.Color ? mood[k].clone() : v; }
  const U = {uTime: {value: 0}, uPix: {value: 1}};

  // Backdrop
  const back = new THREE.Mesh(new THREE.SphereGeometry(60, 48, 24), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false,
    uniforms: {uTime: U.uTime, uGlow: {value: mood.glow}, uKey: {value: mood.key}, uHaze: {value: 1}, uGlowDir: {value: new THREE.Vector3(0.05, 0.12, -1).normalize()}},
    vertexShader: `varying vec3 vDir; void main(){ vDir = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
    fragmentShader: NOISE + /* glsl */`
      uniform float uTime, uHaze; uniform vec3 uGlow, uKey, uGlowDir; varying vec3 vDir;
      void main(){
        vec3 d = normalize(vDir);
        vec3 col = mix(vec3(0.0012, 0.0008, 0.0007), vec3(0.0045, 0.0028, 0.0021), smoothstep(-0.4, 0.5, d.y));
        float g = pow(max(dot(d, uGlowDir), 0.0), 5.0);
        float n = noise(d * 3.2 + vec3(0.0, uTime * 0.025, uTime * 0.018)) * 0.65 + noise(d * 8.0 - uTime * 0.04) * 0.35;
        col += uGlow * (g * 0.035 + n * (0.004 + g * 0.022)) * uHaze;
        // Light through a slatted blind, thrown on the far wall behind the head.
        vec3 gd = normalize(vec3(0.3, 0.3, -1.0));
        vec3 gr = normalize(cross(gd, vec3(0.0, 1.0, 0.0))), gu = cross(gr, gd);
        vec2 q = vec2(dot(d, gr), dot(d, gu)) / max(dot(d, gd), 0.001);
        q.x -= q.y * 0.5;
        float win = smoothstep(0.3, 0.2, abs(q.x)) * smoothstep(0.24, 0.15, abs(q.y)) * step(0.0, dot(d, gd));
        float slat = smoothstep(0.42, 0.58, 0.5 + 0.5 * sin(q.y * 84.0));
        float breakup = 0.55 + 0.45 * noise(vec3(q * 6.0, uTime * 0.05));
        col += uKey * win * slat * breakup * 0.022 * uHaze;
        gl_FragColor = vec4(col, 1.0);
        #include <colorspace_fragment>
      }`,
  }));
  scene.add(back);

  // Beams
  function beam(apex, aim, radius, length){
    const geo = new THREE.ConeGeometry(radius, length, 64, 12, true);
    // Apex at the origin, pointing down -y
    geo.translate(0, -length / 2, 0);
    const mat = new THREE.ShaderMaterial({
      transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, side: THREE.DoubleSide,
      uniforms: {uTime: U.uTime, uColor: {value: new THREE.Color()}, uAmt: {value: 1}, uLen: {value: length}},
      vertexShader: /* glsl */`
        varying float vAlong; varying vec3 vN; varying vec3 vV; varying vec3 vLocal;
        uniform float uLen;
        void main(){
          vAlong = -position.y / uLen; vLocal = position;
          vec4 mv = modelViewMatrix * vec4(position, 1.0);
          vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz);
          gl_Position = projectionMatrix * mv;
        }`,
      fragmentShader: NOISE + /* glsl */`
        uniform float uTime, uAmt; uniform vec3 uColor;
        varying float vAlong; varying vec3 vN; varying vec3 vV; varying vec3 vLocal;
        void main(){
          float face = pow(abs(dot(normalize(vN), normalize(vV))), 2.2);
          float fall = smoothstep(0.0, 0.08, vAlong) * pow(1.0 - vAlong, 1.4);
          float haze = 0.55 + 0.45 * noise(vLocal * 1.4 + vec3(0.0, uTime * 0.35, uTime * 0.12));
          // Rays: the light is combed into shafts running down the beam, drifting slowly round it.
          vec2 ring = normalize(vLocal.xz + 0.0001);
          float rays = 0.5 + 0.5 * noise(vec3(ring * 3.4, vAlong * 1.2 - uTime * 0.05));
          rays = mix(0.45, 1.25, rays * rays);
          float a = face * fall * haze * rays * 0.11 * uAmt;
          gl_FragColor = vec4(uColor, a);
          #include <colorspace_fragment>
        }`,
    });
    const m = new THREE.Mesh(geo, mat);
    m.position.copy(apex);
    const dir = new THREE.Vector3().subVectors(aim, apex).normalize();
    m.quaternion.setFromUnitVectors(new THREE.Vector3(0, -1, 0), dir);
    m.userData = {apex: apex.clone(), dir, slope: radius / length};
    m.renderOrder = 5;
    scene.add(m);
    return m;
  }
  const key = beam(new THREE.Vector3(-3.6, 7.6, 1.2), new THREE.Vector3(-0.35, -2.3, 0.2), 1.45, 13);
  const rim = beam(new THREE.Vector3(5.2, 6.2, -3.4), new THREE.Vector3(-0.2, -2.0, 0), 1.6, 12);

  // Dust
  const dustN = o.dust, dPos = new Float32Array(dustN * 3), dRand = new Float32Array(dustN * 3);
  for (let i = 0; i < dustN; i++){
    dPos[i * 3] = (Math.random() - 0.5) * 11; dPos[i * 3 + 1] = -2.2 + Math.random() * 8; dPos[i * 3 + 2] = (Math.random() - 0.5) * 9;
    dRand[i * 3] = Math.random(); dRand[i * 3 + 1] = Math.random(); dRand[i * 3 + 2] = Math.random();
  }
  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute("position", new THREE.BufferAttribute(dPos, 3));
  dustGeo.setAttribute("aRand", new THREE.BufferAttribute(dRand, 3));
  const dustMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    uniforms: {uTime: U.uTime, uPix: U.uPix, uColor: {value: mood.key}, uAmt: {value: 1},
      uA: {value: key.userData.apex}, uDir: {value: key.userData.dir}, uSlope: {value: key.userData.slope},
      uB: {value: rim.userData.apex}, uDirB: {value: rim.userData.dir}, uSlopeB: {value: rim.userData.slope}, uRim: {value: mood.rim}},
    vertexShader: /* glsl */`
      uniform float uTime, uPix, uSlope, uSlopeB; uniform vec3 uA, uDir, uB, uDirB;
      attribute vec3 aRand; varying float vLit; varying float vRim; varying float vA;
      float inBeam(vec3 p, vec3 a, vec3 d, float slope){
        vec3 ap = p - a; float along = dot(ap, d); float r = along * slope;
        return along > 0.0 ? 1.0 - smoothstep(r * 0.55, r, length(ap - d * along)) : 0.0;
      }
      void main(){
        vec3 p = position;
        float t = uTime * (0.05 + aRand.x * 0.08);
        p += vec3(sin(t * 3.1 + aRand.y * 6.28), sin(t * 2.3 + aRand.z * 6.28) * 0.6 - fract(uTime * 0.004 * (0.3 + aRand.z)) * 0.8, cos(t * 2.7 + aRand.x * 6.28)) * 0.45;
        vLit = inBeam(p, uA, uDir, uSlope); vRim = inBeam(p, uB, uDirB, uSlopeB);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vA = 0.035 + vLit * 0.75 + vRim * 0.45;
        gl_PointSize = (0.8 + aRand.z * 2.2) * uPix * (3.5 / -mv.z) * (1.0 + vLit * 0.8);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 uColor, uRim; uniform float uAmt; varying float vLit; varying float vRim; varying float vA;
      void main(){
        float d = length(gl_PointCoord - 0.5); float a = smoothstep(0.5, 0.0, d);
        gl_FragColor = vec4(mix(uColor, uRim, vRim / (vLit + vRim + 0.001)), a * vA * uAmt);
        #include <colorspace_fragment>
      }`,
  });
  const dust = new THREE.Points(dustGeo, dustMat);
  dust.renderOrder = 6; dust.frustumCulled = false;
  scene.add(dust);

  // Bokeh Motes close to the lens, far out of focus: soft six-sided discs with a brighter rim, the shape a real aperture gives them.
  const bN = o.bokeh, bPos = new Float32Array(bN * 3), bRand = new Float32Array(bN * 3);
  for (let i = 0; i < bN; i++){
    bPos[i * 3] = (Math.random() - 0.5) * 7; bPos[i * 3 + 1] = -1.8 + Math.random() * 4.4; bPos[i * 3 + 2] = 1.6 + Math.random() * 3.2;
    bRand[i * 3] = Math.random(); bRand[i * 3 + 1] = Math.random(); bRand[i * 3 + 2] = Math.random();
  }
  const bokehGeo = new THREE.BufferGeometry();
  bokehGeo.setAttribute("position", new THREE.BufferAttribute(bPos, 3));
  bokehGeo.setAttribute("aRand", new THREE.BufferAttribute(bRand, 3));
  const bokehMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, depthTest: false, blending: THREE.AdditiveBlending,
    uniforms: {uTime: U.uTime, uPix: U.uPix, uKey: {value: mood.key}, uRim: {value: mood.rim}, uAmt: {value: 1}},
    vertexShader: /* glsl */`
      uniform float uTime, uPix; attribute vec3 aRand; varying float vA; varying float vRim; varying float vSpin;
      void main(){
        vec3 p = position;
        float t = uTime * (0.03 + aRand.x * 0.04);
        p += vec3(sin(t * 2.3 + aRand.y * 6.28) * 0.5, sin(t * 1.7 + aRand.z * 6.28) * 0.35, cos(t * 1.9 + aRand.x * 6.28) * 0.3);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        float z = -mv.z;
        // Nearest the lens they are biggest and faintest; past the head they are gone.
        vA = smoothstep(0.35, 1.2, z) * (1.0 - smoothstep(3.2, 5.5, z)) * (0.35 + 0.65 * aRand.z) * (0.55 + 0.45 * sin(uTime * (0.4 + aRand.y) + aRand.x * 9.0));
        vRim = aRand.y; vSpin = aRand.x * 1.0472;
        gl_PointSize = clamp((26.0 + aRand.z * 40.0) * uPix * (3.0 / max(z, 0.4)), 4.0, 180.0 * uPix);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      uniform vec3 uKey, uRim; uniform float uAmt; varying float vA; varying float vRim; varying float vSpin;
      void main(){
        vec2 p = (gl_PointCoord - 0.5) * 2.0;
        float c = cos(vSpin), s = sin(vSpin); p = abs(mat2(c, -s, s, c) * p);
        float hex = max(p.x * 0.866 + p.y * 0.5, p.y);
        float disc = smoothstep(1.0, 0.9, hex);
        float edge = smoothstep(0.62, 0.96, hex) * disc;
        vec3 col = mix(uKey, uRim, step(0.72, vRim));
        gl_FragColor = vec4(col, (disc * 0.05 + edge * 0.07) * vA * uAmt);
        #include <colorspace_fragment>
      }`,
  });
  const bokeh = new THREE.Points(bokehGeo, bokehMat);
  bokeh.renderOrder = 8; bokeh.frustumCulled = false;
  scene.add(bokeh);

  // Floor and the mark
  const floorMat = new THREE.ShaderMaterial({
    transparent: true, depthWrite: false,
    uniforms: {uTime: U.uTime, uKey: {value: mood.key}, uAmt: {value: 1}, uSpot: {value: 0}, uMark: {value: new THREE.Color("#FF7A3D")}, uLamp: {value: key.userData.apex}},
    vertexShader: `varying vec3 vW; void main(){ vec4 w = modelMatrix * vec4(position, 1.0); vW = w.xyz; gl_Position = projectionMatrix * viewMatrix * w; }`,
    fragmentShader: NOISE + /* glsl */`
      uniform float uAmt, uSpot; uniform vec3 uKey, uMark, uLamp; varying vec3 vW;
      float box(vec2 p, vec2 c, vec2 h){ vec2 d = abs(p - c) - h; return length(max(d, 0.0)) + min(max(d.x, d.y), 0.0); }
      float line(float d, float w){ return 1.0 - smoothstep(w * 0.5, w * 0.5 + fwidth(d) * 1.2, abs(d)); }
      void main(){
        vec2 p = vW.xz;
        float pool = exp(-dot(p - vec2(0.05, 0.35), p - vec2(0.05, 0.35)) * 0.42);
        float spot = exp(-dot(p - vec2(0.0, 1.9), p - vec2(0.0, 1.9)) * 1.8) * uSpot;
        float scuff = noise(vec3(p * 9.0, 0.0));
        float grain = 0.8 + 0.2 * scuff;
        vec3 c = uKey * (pool * 0.07 + spot * 0.22) * grain * uAmt;
        // A polished floor: the key lamp's glint, sliding as the camera moves.
        vec3 V = normalize(cameraPosition - vW), L = normalize(uLamp - vW);
        float glint = pow(max(normalize(V + L).y, 0.0), 90.0) * (0.35 + 0.65 * noise(vec3(p * 2.2, 3.0)));
        c += uKey * glint * 0.16 * uAmt;
        // The dolly track the camera rides on, laid toward the mark.
        float track = smoothstep(2.25, 2.45, p.y) * (1.0 - smoothstep(6.0, 8.5, p.y));
        float rails = max(line(p.x - 0.46, 0.022), line(p.x + 0.46, 0.022)) * track;
        float sleepers = line((fract(p.y / 0.34) - 0.5) * 0.34, 0.05) * step(abs(p.x), 0.6) * track;
        c += uKey * rails * (0.03 + pool * 0.25 + glint * 0.6) * uAmt + vec3(0.010, 0.0065, 0.005) * sleepers * uAmt;
        // A cable snaking in from the dark, and two other actors' marks.
        float cable = line(p.x - (1.35 + 0.32 * sin(p.y * 1.4 + 0.6)), 0.03) * step(p.y, 1.4);
        c += uKey * cable * (0.012 + pool * 0.08) * uAmt;
        vec2 m1 = p - vec2(-1.55, 0.95), m2 = p - vec2(1.7, 1.3);
        float x1 = max(line(m1.x - m1.y, 0.035), line(m1.x + m1.y, 0.035)) * step(max(abs(m1.x), abs(m1.y)), 0.12);
        float x2 = max(line(m2.x - m2.y, 0.035), line(m2.x + m2.y, 0.035)) * step(max(abs(m2.x), abs(m2.y)), 0.12);
        c += (vec3(0.49, 0.91, 0.78) * x1 + vec3(0.66, 0.72, 1.0) * x2) * (0.05 + pool * 0.12) * uAmt;
        // The mark: a T of tape where the candidate stands.
        float t = min(box(p, vec2(0.0, 1.9), vec2(0.34, 0.035)), box(p, vec2(0.0, 2.06), vec2(0.035, 0.17)));
        float tape = 1.0 - smoothstep(0.0, 0.012, t);
        c += uMark * tape * (0.1 + 0.55 * uSpot + pool * 0.3) * uAmt;
        float fade = 1.0 - smoothstep(4.0, 9.0, length(p));
        gl_FragColor = vec4(c, fade);
        #include <colorspace_fragment>
      }`,
  });
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(22, 22), floorMat);
  floor.rotation.x = -Math.PI / 2; floor.position.y = -2.3; floor.renderOrder = 0;
  scene.add(floor);

  // Post-processing (home only)
  let composer = null, bloom = null, grade = null;
  if (o.post){
    const [{EffectComposer}, {RenderPass}, {UnrealBloomPass}, {ShaderPass}, {OutputPass}] = await Promise.all([
      import("three/addons/postprocessing/EffectComposer.js"), import("three/addons/postprocessing/RenderPass.js"),
      import("three/addons/postprocessing/UnrealBloomPass.js"), import("three/addons/postprocessing/ShaderPass.js"),
      import("three/addons/postprocessing/OutputPass.js")]);
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));
    bloom = new UnrealBloomPass(new THREE.Vector2(256, 256), 0.62, 0.5, 0.5);
    composer.addPass(bloom);
    // The film stock: the frame weaves a hair in the gate, the exposure flickers at 24 frames a second, the brightest points throw the long blue streaks of an anamorphic lens, and grain sits in the shadows.
    grade = new ShaderPass({
      uniforms: {tDiffuse: {value: null}, uVig: {value: 0.55}, uCA: {value: 0.9}, uTime: U.uTime, uRes: {value: new THREE.Vector2(1, 1)}},
      vertexShader: `varying vec2 vUv; void main(){ vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`,
      fragmentShader: /* glsl */`
        uniform sampler2D tDiffuse; uniform float uVig, uCA, uTime; uniform vec2 uRes; varying vec2 vUv;
        float h21(vec2 p){ p = fract(p * vec2(123.34, 456.21)); p += dot(p, p + 45.32); return fract(p.x * p.y); }
        void main(){
          float frame = floor(uTime * 24.0);
          vec2 uv = vUv + (vec2(h21(vec2(frame, 1.3)), h21(vec2(frame, 7.1))) - 0.5) * vec2(0.0005, 0.0009);
          vec2 dc = uv - 0.5; float r2 = dot(dc, dc);
          vec2 off = dc * r2 * 0.02 * uCA;
          vec3 c = vec3(texture2D(tDiffuse, uv + off).r, texture2D(tDiffuse, uv).g, texture2D(tDiffuse, uv - off).b);
          vec3 streak = vec3(0.0);
          for (int i = 1; i <= 7; i++){
            float fi = float(i), o = fi * fi * 0.0026;
            vec3 s = texture2D(tDiffuse, uv + vec2(o, 0.0)).rgb + texture2D(tDiffuse, uv - vec2(o, 0.0)).rgb;
            streak += max(s - 0.62, 0.0) * (1.0 - fi / 8.0);
          }
          c += dot(streak, vec3(0.33)) * vec3(0.42, 0.55, 1.0) * 0.07;
          c *= mix(1.0 - uVig, 1.0, smoothstep(0.75, 0.1, r2 * 1.7));
          c *= 1.0 + (h21(vec2(frame, 3.7)) - 0.5) * 0.03;
          float luma = dot(c, vec3(0.299, 0.587, 0.114));
          c += (h21(vUv * uRes + frame * 17.0) - 0.5) * 0.012 * (1.0 - smoothstep(0.0, 0.5, luma));
          gl_FragColor = vec4(c, 1.0);
        }`,
    });
    composer.addPass(grade);
    composer.addPass(new OutputPass());
  }

  // Camera rig
  const rig = {pos: camera.position.clone(), look: new THREE.Vector3(0, 0.2, 0), fov: 34, ease: 2.6};
  const lookNow = rig.look.clone(), parallax = new THREE.Vector2(), par = new THREE.Vector2();
  // Where the scene's centre sits on screen, as a fraction off centre
  let viewShift = [0, 0];

  // Size and quality
  let W = 1, H = 1, dpr = Math.min(o.maxDpr, devicePixelRatio || 1), useBloom = !!o.post;
  function size(){
    W = Math.max(1, canvas.clientWidth || innerWidth); H = Math.max(1, canvas.clientHeight || innerHeight);
    renderer.setPixelRatio(dpr); renderer.setSize(W, H, false);
    if (composer){ composer.setPixelRatio(dpr); composer.setSize(W, H); grade.uniforms.uRes.value.set(W * dpr, H * dpr); }
    U.uPix.value = dpr;
    for (const f of sizeFns) f(W, H, dpr);
    frameCamera();
  }
  function frameCamera(){
    camera.aspect = W / H;
    if (viewShift[0] || viewShift[1]) camera.setViewOffset(W, H, -viewShift[0] * W, -viewShift[1] * H, W, H);
    else camera.clearViewOffset();
    camera.updateProjectionMatrix();
  }
  const sizeFns = [];
  new ResizeObserver(size).observe(canvas);

  let slow = 0, fast = 0, ema = 16;
  function tune(ms){
    ema += (ms - ema) * 0.05;
    const budget = 1000 / Math.min(o.fps, 60);
    if (ema > budget * 1.45){ slow++; fast = 0; } else if (ema < budget * 0.9){ fast++; slow = 0; } else { slow = Math.max(0, slow - 1); fast = Math.max(0, fast - 1); }
    if (slow > 90){
      slow = 0;
      if (dpr > 0.8){ dpr = Math.max(0.75, dpr - 0.25); size(); }
      else if (useBloom){ useBloom = false; }
    } else if (fast > 240 && dpr < Math.min(o.maxDpr, devicePixelRatio || 1)){
      fast = 0; dpr = Math.min(o.maxDpr, dpr + 0.125); size();
    }
  }

  // The loop
  const frames = [], afters = [];
  let active = true, last = 0, t = 0, pending = 0, timeScale = 1, moving = true;
  const colourStep = (a, b, k) => a.lerp(b, k);
  function step(now){
    if (!o.manual) pending = requestAnimationFrame(step);
    if (!active || document.hidden) { last = now; return; }
    // A quiet screen may draw at its own low rate, but whenever the camera is travelling or the light is changing it draws every frame, so a move never stutters.
    const gap = 1000 / (moving ? Math.max(o.fps, 60) : o.fps);
    if (last && now - last < gap - 3) return;
    const real = last ? Math.min(0.1, (now - last) / 1000) : 1 / 60;
    const dt = real * timeScale;
    last = now; t += dt;
    U.uTime.value = t;

    const k = 1 - Math.exp(-real * 1.6);
    let change = 0;
    for (const name of Object.keys(want)){
      if (mood[name] instanceof THREE.Color){ change += Math.abs(mood[name].r - want[name].r) + Math.abs(mood[name].g - want[name].g); colourStep(mood[name], want[name], k); }
      else { change += Math.abs(want[name] - mood[name]); mood[name] += (want[name] - mood[name]) * k; }
    }
    back.material.uniforms.uHaze.value = mood.haze;
    key.material.uniforms.uColor.value.copy(mood.key); key.material.uniforms.uAmt.value = mood.beam;
    rim.material.uniforms.uColor.value.copy(mood.rim); rim.material.uniforms.uAmt.value = mood.rimBeam;
    dustMat.uniforms.uAmt.value = 0.6 + mood.haze * 0.4;
    bokehMat.uniforms.uAmt.value = 0.5 + mood.beam * 0.5;
    floorMat.uniforms.uAmt.value = mood.floor;

    par.lerp(parallax, 1 - Math.exp(-real * 2.5));
    const c = 1 - Math.exp(-real * rig.ease);
    change += camera.position.distanceTo(rig.pos) + lookNow.distanceTo(rig.look) + Math.abs(rig.fov - camera.fov) * 0.05 + par.distanceTo(parallax);
    moving = change > 0.004;
    camera.position.lerp(rig.pos, c);
    camera.position.x += par.x * 0.18; camera.position.y += par.y * 0.1;
    lookNow.lerp(rig.look, c);
    camera.fov += (rig.fov - camera.fov) * c;
    camera.lookAt(lookNow);
    camera.updateProjectionMatrix();

    for (const f of frames) f(dt, t);
    const t0 = performance.now();
    if (composer && useBloom) composer.render(dt);
    else renderer.render(scene, camera);
    for (const f of afters) f(renderer, dt, t);
    camera.position.x -= par.x * 0.18; camera.position.y -= par.y * 0.1;
    tune(performance.now() - t0 + 4);
  }

  size();
  if (!o.manual) pending = requestAnimationFrame(step);

  return {
    THREE, renderer, scene, camera, rig, key, rim, floor, dust,
    get size(){ return [W, H]; },
    get pixelRatio(){ return dpr; },
    onFrame(fn){ frames.push(fn); },
    afterRender(fn){ afters.push(fn); },
    onResize(fn){ sizeFns.push(fn); fn(W, H, dpr); },
    setMood(name){ const m = MOODS[name]; if (!m) return; for (const [k2, v] of Object.entries(m)) want[k2] = typeof v === "string" ? new THREE.Color(v) : v; },
    setSpot(v){ floorMat.uniforms.uSpot.value = v; },
    setParallax(x, y){ parallax.set(x, y); },
    setViewShift(x, y){ viewShift = [x, y]; frameCamera(); },
    setActive(on){ active = on; if (on) last = 0; },
    setFps(f){ o.fps = f; },
    render(now){ step(now); },
    set moving(on){ if (on) moving = true; },
    set timeScale(v){ timeScale = v; },
    set useBloom(on){ useBloom = !!composer && on; },
    set bloomStrength(v){ if (bloom) bloom.strength = v; },
    stop(){ cancelAnimationFrame(pending); },
  };
}
