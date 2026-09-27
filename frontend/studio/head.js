// head.js
// The interviewer: a head drawn in light.
//
// Its face is the canonical face mesh the camera analysis measures you with (facemesh.js), so the thing asking the questions is built from the same 468 points that later judge where you looked.
// Three layers share one set of points: a dark glass surface lit from the key light, the mesh's edges as faint wire, and the points themselves.
// A sparse shell of points behind the face and a few down the neck turn a mask into a head.
//
// It looks where it is told (look), talks for as long as it is told (speak), and has three moods beside idle: attentive, listening and thinking.
// A scan line runs down the face the whole time, faster while it listens.

import * as THREE from "three";
import {POSITIONS, TRIANGLES} from "./facemesh.js";

const CM = 0.1;                                    // centimetres to scene units
const EYES = [[33, 133, 159, 145], [362, 263, 386, 374]];
const UPPER_LIP = 13, LOWER_LIP = 14;

const COMMON = /* glsl */`
  uniform float uTime, uMouth, uScan, uDim, uListen, uAbsorb, uPix;
  uniform vec3 uWarm, uCool, uHot;
  attribute float aJaw;
  vec3 jaw(vec3 p){
    // The jaw drops and comes forward a little, the way a mouth opens.
    p.y -= uMouth * aJaw * 0.30;
    p.z += uMouth * aJaw * 0.04;
    return p;
  }
  float scanBand(float y){ return exp(-pow((y - uScan) * 10.0, 2.0)); }
`;

function pointsMaterial(uniforms){
  return new THREE.ShaderMaterial({
    uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: COMMON + /* glsl */`
      attribute float aSeed, aKind;
      varying vec3 vColor; varying float vAlpha;
      void main(){
        vec3 p = jaw(position);
        float face = 1.0 - step(0.5, aKind);
        p += normalize(p + 0.0001) * sin(uTime * 1.3 + aSeed * 40.0) * 0.004 * (1.0 + aKind * 2.0);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        float band = scanBand(p.y) * face;
        float twinkle = 0.7 + 0.3 * sin(uTime * 2.1 + aSeed * 91.0);
        float neck = aKind > 1.5 ? smoothstep(-2.2, -1.1, p.y) : 1.0;
        vAlpha = (face * (0.85 + uMouth * 0.5) + (1.0 - face) * 0.2 * neck) * twinkle + band * 0.9 + uAbsorb * 0.5 * face;
        vAlpha *= uDim;
        vec3 c = mix(uWarm, uCool, clamp(0.25 + p.y * 0.18, 0.0, 1.0) * 0.5 + uListen * 0.45);
        vColor = mix(c, uHot, band * 0.85 + uAbsorb * 0.4);
        gl_PointSize = mix(1.7, 2.6, face) * (1.0 + band * 1.3) * uPix * (5.0 / -mv.z);
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      varying vec3 vColor; varying float vAlpha;
      void main(){
        float d = length(gl_PointCoord - 0.5);
        float a = smoothstep(0.5, 0.05, d);
        gl_FragColor = vec4(vColor, a * a * vAlpha);
        #include <colorspace_fragment>
      }`,
  });
}

function wireMaterial(uniforms){
  return new THREE.ShaderMaterial({
    uniforms, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: COMMON + /* glsl */`
      varying float vBand; varying float vY;
      void main(){
        vec3 p = jaw(position);
        vBand = scanBand(p.y); vY = p.y;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
      }`,
    fragmentShader: /* glsl */`
      uniform float uDim, uListen, uSpeakGlow; uniform vec3 uWarm, uCool, uHot;
      varying float vBand; varying float vY;
      void main(){
        vec3 c = mix(uWarm, uCool, 0.3 + uListen * 0.5);
        gl_FragColor = vec4(mix(c, uHot, vBand), (0.13 + vBand * 0.6 + uSpeakGlow * 0.12) * uDim);
        #include <colorspace_fragment>
      }`,
  });
}

function glassMaterial(uniforms){
  // Dark glass: lit from the key light above and to the left, with a cool rim where the surface turns away from the camera.
  return new THREE.ShaderMaterial({
    uniforms, transparent: true, side: THREE.DoubleSide,
    vertexShader: COMMON + /* glsl */`
      varying vec3 vN; varying vec3 vV; varying float vY; varying float vZ;
      void main(){
        vec3 p = jaw(position);
        vec4 mv = modelViewMatrix * vec4(p, 1.0);
        vN = normalize(normalMatrix * normal); vV = normalize(-mv.xyz); vY = p.y; vZ = p.z;
        gl_Position = projectionMatrix * mv;
      }`,
    fragmentShader: /* glsl */`
      uniform float uDim, uListen, uScan, uSpeakGlow; uniform vec3 uWarm, uCool, uHot;
      varying vec3 vN; varying vec3 vV; varying float vY; varying float vZ;
      void main(){
        vec3 n = normalize(vN); if (dot(n, vV) < 0.0) n = -n;
        float key = max(dot(n, normalize(vec3(-0.55, 0.65, 0.55))), 0.0);
        float rim = pow(1.0 - max(dot(n, vV), 0.0), 3.0);
        float band = exp(-pow((vY - uScan) * 10.0, 2.0));
        vec3 base = vec3(0.012, 0.008, 0.006);
        vec3 c = base + uWarm * key * key * (0.045 + uSpeakGlow * 0.05) + mix(uCool, uWarm, 0.3 - uListen * 0.3) * rim * (0.62 + uSpeakGlow * 0.3) + uHot * band * 0.12;
        // Depth contours, like a sculptor's section lines, brightest where the scan passes.
        float f = vZ * 16.0, iso = 1.0 - min(abs(fract(f - 0.5) - 0.5) / max(fwidth(f), 0.0001), 1.0);
        c += mix(uWarm, uHot, band) * iso * (0.018 + band * 0.2 + key * 0.03 + uSpeakGlow * 0.02);
        gl_FragColor = vec4(c, (0.82 + rim * 0.18) * uDim);
        #include <colorspace_fragment>
      }`,
  });
}

// A spiral of points over the back of an ellipsoid: the skull the mesh leaves out.
function shellPoints(count){
  const out = [], golden = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < count; i++){
    const y = 1 - (i / (count - 1)) * 2, r = Math.sqrt(1 - y * y), th = golden * i;
    const x = Math.cos(th) * r, z = Math.sin(th) * r;
    const p = [x * 7.9, y * 10.2 + 0.9, z * 8.2 - 1.2];
    // The face covers the front; nothing under the jaw
    if (p[2] > 1.2 || p[1] < -7.4) continue;
    out.push(p);
  }
  return out;
}

function neckPoints(count){
  const out = [];
  for (let i = 0; i < count; i++){
    const a = Math.random() * Math.PI * 2, y = -8 - Math.random() * 9;
    const r = 4.4 + (y < -13 ? (-13 - y) * 1.6 : 0);
    out.push([Math.cos(a) * r, y, Math.sin(a) * r * 0.85 - 1.6]);
  }
  return out;
}

export function createHead({shell = 900, neck = 160} = {}){
  const group = new THREE.Group();
  const face = POSITIONS.length / 3;

  // Centre the face on the eyes, so turning the head turns it about them.
  const eyeY = (POSITIONS[159 * 3 + 1] + POSITIONS[386 * 3 + 1]) / 2;
  const centre = [0, eyeY - 1.2, 1.6];
  const pts = [];
  for (let i = 0; i < face; i++) pts.push([POSITIONS[i * 3], POSITIONS[i * 3 + 1], POSITIONS[i * 3 + 2]]);
  const extra = shellPoints(shell).concat(neckPoints(neck));
  const all = pts.concat(extra), n = all.length;

  const pos = new Float32Array(n * 3), seed = new Float32Array(n), kind = new Float32Array(n), jawW = new Float32Array(n);
  const mouthY = (POSITIONS[UPPER_LIP * 3 + 1] + POSITIONS[LOWER_LIP * 3 + 1]) / 2;
  all.forEach((p, i) => {
    pos[i * 3] = (p[0] - centre[0]) * CM; pos[i * 3 + 1] = (p[1] - centre[1]) * CM; pos[i * 3 + 2] = (p[2] - centre[2]) * CM;
    seed[i] = Math.random();
    kind[i] = i < face ? 0 : i < face + (all.length - face - neck) ? 1 : 2;
    if (i < face){
      // Below the mouth, fading out towards the ears: the part a jaw moves.
      const below = THREE.MathUtils.smoothstep(mouthY - p[1], 0.05, 0.9);
      const side = 1 - THREE.MathUtils.smoothstep(Math.abs(p[0]), 3.2, 6.8);
      jawW[i] = below * side;
    }
  });

  const uniforms = {
    uTime: {value: 0}, uMouth: {value: 0}, uScan: {value: 1}, uDim: {value: 1}, uListen: {value: 0},
    uAbsorb: {value: 0}, uPix: {value: 1}, uSpeakGlow: {value: 0},
    uWarm: {value: new THREE.Color("#FFB07A")}, uCool: {value: new THREE.Color("#A9B8FF")}, uHot: {value: new THREE.Color("#FFE3C4")},
  };

  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  geo.setAttribute("aSeed", new THREE.BufferAttribute(seed, 1));
  geo.setAttribute("aKind", new THREE.BufferAttribute(kind, 1));
  geo.setAttribute("aJaw", new THREE.BufferAttribute(jawW, 1));
  geo.computeBoundingSphere();

  // The face surface and its wire use the first 468 points only.
  const faceGeo = new THREE.BufferGeometry();
  faceGeo.setAttribute("position", new THREE.BufferAttribute(pos.slice(0, face * 3), 3));
  faceGeo.setAttribute("aJaw", new THREE.BufferAttribute(jawW.slice(0, face), 1));
  faceGeo.setIndex(TRIANGLES);
  faceGeo.computeVertexNormals();
  const glass = new THREE.Mesh(faceGeo, glassMaterial(uniforms));
  glass.renderOrder = 1;

  const edges = new Set(), pairs = [];
  for (let t = 0; t < TRIANGLES.length; t += 3){
    for (const [a, b] of [[0, 1], [1, 2], [2, 0]]){
      const i = TRIANGLES[t + a], j = TRIANGLES[t + b], key = i < j ? i * 1000 + j : j * 1000 + i;
      if (!edges.has(key)){ edges.add(key); pairs.push(i, j); }
    }
  }
  const wireGeo = new THREE.BufferGeometry();
  wireGeo.setAttribute("position", faceGeo.getAttribute("position"));
  wireGeo.setAttribute("aJaw", faceGeo.getAttribute("aJaw"));
  wireGeo.setIndex(pairs);
  const wire = new THREE.LineSegments(wireGeo, wireMaterial(uniforms));
  wire.renderOrder = 2;

  const points = new THREE.Points(geo, pointsMaterial(uniforms));
  points.renderOrder = 3;

  // Eyes: two brighter points that follow the look target inside their sockets.
  const eyeCentres = EYES.map(ids => {
    const c = [0, 0, 0];
    ids.forEach(k => { for (let a = 0; a < 3; a++) c[a] += POSITIONS[k * 3 + a] / ids.length; });
    return new THREE.Vector3((c[0] - centre[0]) * CM, (c[1] - centre[1]) * CM, (c[2] - centre[2]) * CM + 0.02);
  });
  const eyeGeo = new THREE.BufferGeometry();
  eyeGeo.setAttribute("position", new THREE.BufferAttribute(new Float32Array(6), 3));
  const eyeMat = new THREE.ShaderMaterial({
    uniforms: {uPix: uniforms.uPix, uOpen: {value: 1}, uDim: uniforms.uDim, uColor: {value: new THREE.Color("#FFD9B0")}},
    transparent: true, depthWrite: false, blending: THREE.AdditiveBlending,
    vertexShader: /* glsl */`
      uniform float uPix, uOpen;
      void main(){ vec4 mv = modelViewMatrix * vec4(position, 1.0); gl_PointSize = 9.0 * uPix * (5.0 / -mv.z) * (0.35 + 0.65 * uOpen); gl_Position = projectionMatrix * mv; }`,
    fragmentShader: /* glsl */`
      uniform float uOpen, uDim; uniform vec3 uColor;
      void main(){
        vec2 c = gl_PointCoord - 0.5; c.y /= max(uOpen, 0.08);
        float d = length(c); float a = smoothstep(0.5, 0.0, d);
        gl_FragColor = vec4(uColor, a * a * uDim * (0.4 + 0.6 * uOpen));
        #include <colorspace_fragment>
      }`,
  });
  const eyes = new THREE.Points(eyeGeo, eyeMat);
  eyes.renderOrder = 4;

  // Rings that leave the mouth while it speaks.
  const mouth = new THREE.Vector3(0, (mouthY - centre[1]) * CM, (POSITIONS[UPPER_LIP * 3 + 2] - centre[2]) * CM + 0.05);
  const rings = [];
  for (let i = 0; i < 5; i++){
    const m = new THREE.Mesh(new THREE.RingGeometry(0.2, 0.212, 96), new THREE.MeshBasicMaterial({
      color: new THREE.Color("#FFB07A"), transparent: true, opacity: 0, blending: THREE.AdditiveBlending, depthWrite: false, toneMapped: false}));
    m.position.copy(mouth); m.userData.born = -99; rings.push(m); group.add(m);
  }

  group.add(glass, wire, points, eyes);

  // Behaviour
  const target = new THREE.Vector2(), gaze = new THREE.Vector2();
  let speakUntil = 0, mode = "idle", level = 0, absorb = 0, blinkAt = 2, blink = 0, nextRing = 0, clock = 0, mouthNow = 0;

  function syllables(t){
    // A voice's envelope: syllables of uneven length, grouped into phrases.
    const phrase = 0.55 + 0.45 * Math.sin(t * 1.9 + Math.sin(t * 0.7) * 2.0);
    const syl = Math.max(0, Math.sin(t * 13.0 + Math.sin(t * 5.3) * 1.6));
    return Math.min(1, syl * phrase * 1.25);
  }

  function update(dt, t){
    clock = t;
    uniforms.uTime.value = t;
    const k = 1 - Math.exp(-dt * (mode === "listening" ? 3 : 5));
    gaze.x += (target.x - gaze.x) * k; gaze.y += (target.y - gaze.y) * k;
    const think = mode === "thinking" ? 1 : 0;
    group.rotation.y = gaze.x * 0.55 + Math.sin(t * 0.31) * 0.035 + think * 0.22;
    group.rotation.x = -gaze.y * 0.3 + Math.sin(t * 0.47) * 0.018 + think * 0.1 + (mode === "listening" ? Math.sin(t * 2.2) * 0.012 * (0.4 + level) : 0);
    group.rotation.z = Math.sin(t * 0.23) * 0.015 - think * 0.04;

    const speaking = t < speakUntil;
    const want = speaking ? syllables(t) : 0;
    mouthNow += (want - mouthNow) * (1 - Math.exp(-dt * 22));
    uniforms.uMouth.value = mouthNow;
    uniforms.uSpeakGlow.value += ((speaking ? 1 : 0) - uniforms.uSpeakGlow.value) * (1 - Math.exp(-dt * 3));

    const scanSpeed = mode === "listening" ? 0.55 : mode === "thinking" ? 0.9 : 0.22;
    uniforms.uScan.value = 1.05 - ((t * scanSpeed) % 1.35) * 1.9;
    const listenTo = mode === "listening" ? 0.55 + level * 0.45 : mode === "thinking" ? 0.35 : 0;
    uniforms.uListen.value += (listenTo - uniforms.uListen.value) * (1 - Math.exp(-dt * 4));
    absorb = Math.max(0, absorb - dt * 1.6); uniforms.uAbsorb.value = absorb;

    if (t > blinkAt){ blink = 1; blinkAt = t + 2.2 + Math.random() * 4; }
    blink = Math.max(0, blink - dt * 7);
    eyeMat.uniforms.uOpen.value = 1 - Math.sin(blink * Math.PI);

    // Pupils sit in their sockets, pulled toward where it is looking.
    const arr = eyeGeo.getAttribute("position").array;
    eyeCentres.forEach((c, i) => {
      arr[i * 3] = c.x + gaze.x * 0.05 * (1 - think); arr[i * 3 + 1] = c.y + gaze.y * 0.035 + think * 0.03; arr[i * 3 + 2] = c.z;
    });
    eyeGeo.getAttribute("position").needsUpdate = true;

    if (speaking && t > nextRing){
      const r = rings.find(m => t - m.userData.born > 1.3);
      if (r){ r.userData.born = t; }
      nextRing = t + 0.3 + Math.random() * 0.15;
    }
    for (const r of rings){
      const age = t - r.userData.born;
      if (age < 0 || age > 1.3){ r.material.opacity = 0; continue; }
      const s = 1 + age * 5.5;
      r.scale.set(s, s, 1);
      r.material.opacity = (1 - age / 1.3) * 0.5 * uniforms.uDim.value;
    }
  }

  return {
    group, uniforms, mouth,
    eyeMid: eyeCentres[0].clone().add(eyeCentres[1]).multiplyScalar(0.5),
    update,
    look(x, y){ target.set(THREE.MathUtils.clamp(x, -1, 1), THREE.MathUtils.clamp(y, -1, 1)); },
    speak(seconds){ speakUntil = Math.max(speakUntil, clock + seconds); },
    hush(){ speakUntil = 0; },
    get speaking(){ return clock < speakUntil; },
    set mode(m){ mode = m; }, get mode(){ return mode; },
    set level(v){ level = Math.max(0, Math.min(1, v)); },
    absorb(){ absorb = 1; },
    set dim(v){ uniforms.uDim.value = v; },
    setPixelRatio(p){ uniforms.uPix.value = p; },
  };
}
