'use strict';
// easyedit film layer.
//   footage  -> WebGL: camera with real motion blur, lens, bloom, grade, glitch/VHS/edge effects
//   depth    -> an optional second video (white = near): background blur, subject pop, parallax,
//               and captions that sit behind the subject
//   captions -> 2D canvas on top (or handed to the shader when they go behind the subject)
// Everything is a pure function of time so any frame can be rendered in isolation.
(() => {
  const E = window.EDIT;
  const W = E.width, H = E.height, FPS = E.fps;
  const video = document.getElementById('footage');
  const depthVideo = document.getElementById('depth');
  const canvas = document.getElementById('fx');
  const fxCtx = canvas.getContext('2d');
  let ctx = fxCtx;   // captions draw here, or into capCanvas when they go behind the subject
  const P = Object.assign({ white: '#fbf8f4' }, E.palette);
  const M = E.montageStart / FPS;
  const END = E.frames / FPS;
  // look settings from the editor
  const L = Object.assign({ grain: 0.05, vignette: 0.42, bars: 0, pulse: 1, flash: 1, shake: 0,
                            capSize: 64, capY: 0.61, capFont: 'caption', capAnim: 'rise', capGlow: 1,
                            blur: 1, bloom: 0.3, lens: 0.25, sharpen: 0.2,
                            dof: 0, pop: 0, parallax: 0, capBehind: 0,
                            dust: 0, flicker: 0, barsDrop: 0,
                            capStyle: 'clean', capMotion: '', capTrack: 2, capDX: 0, capDY: 40, capFromScale: 1, capRot: 0 }, E.look);
  const hex = h => [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255);
  const ACCENT = hex(/^#[0-9a-f]{6}$/i.test(P.gold || '') ? P.gold : '#ffe27a');
  // how a caption word arrives: [from x, from y] in px; wiggle and wave keep moving after that
  const ANIMS = { rise: [0, 14], drop: [0, -46], left: [-80, 0], right: [80, 0], pop: [0, 0], wiggle: [0, 14], wave: [0, 14],
                  custom: [L.capDX, L.capDY] };   // custom: your own start offset, scale and tilt
  const ANIM = ANIMS[L.capAnim] ? L.capAnim : 'rise';
  const MOVES = { punch: 0, whip: 1, slide: 2, 'whip-down': 3, spin: 4, 'zoom-out': 5, bounce: 6,
                  'slide-left': 7, 'slide-right': 8, 'slide-up': 9, 'slide-down': 10, elastic: 11 };

  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const outCubic = x => 1 - Math.pow(1 - clamp(x), 3);
  const outExpo = x => (x = clamp(x)) === 1 ? 1 : 1 - Math.pow(2, -10 * x);
  const inOut = x => (x = clamp(x), x * x * (3 - 2 * x));
  const bell = (x, w) => Math.exp(-(x * x) / (w * w));
  // how a camera move settles into its shot: [curve from 0 to 1, seconds it takes]
  const EASES = {
    expo: [outExpo, 0.28], smooth: [inOut, 0.45], linear: [x => clamp(x), 0.4],
    bounce: [x => { x = clamp(x); const n = 7.5625, d = 2.75;
      return x < 1 / d ? n * x * x : x < 2 / d ? n * (x -= 1.5 / d) * x + 0.75 : x < 2.5 / d ? n * (x -= 2.25 / d) * x + 0.9375 : n * (x -= 2.625 / d) * x + 0.984375; }, 0.6],
    spring: [x => (x = clamp(x)) === 1 ? 1 : 1 - Math.exp(-6 * x) * Math.cos(13 * x), 0.7],
    step: [x => Math.floor(clamp(x) * 4) / 4, 0.4],
  };
  // repeatable noise in -1..1, so split renders match
  const noise = n => { const x = Math.sin(n * 127.1 + 311.7) * 43758.5453; return (x - Math.floor(x)) * 2 - 1; };
  // the same noise, eased between whole numbers: a shake the motion blur can follow
  const smooth = (x, seed) => { const i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return noise(i + seed) * (1 - u) + noise(i + 1 + seed) * u; };

  // grade = [exposure, contrast, saturation, temperature, fade, split amount], shadow tint, highlight tint, extras
  const GRADES = {
    neutral: { g: [0, 1.06, 1.08, 0, 0, 0] },
    warm: { g: [0.01, 1.07, 1.1, 0.07, 0, 0.1], lo: [0.45, 0.3, 0.2], hi: [1, 0.8, 0.55] },
    cool: { g: [0, 1.08, 1.02, -0.07, 0, 0.1], lo: [0.15, 0.3, 0.5], hi: [0.75, 0.9, 1] },
    'teal-orange': { g: [0, 1.1, 1.15, 0, 0, 0.24], lo: [0.0, 0.42, 0.5], hi: [1, 0.66, 0.36] },
    noir: { g: [0, 1.18, 0.4, 0, 0.015, 0] },
    vivid: { g: [0.01, 1.14, 1.38, 0, 0, 0] },
    anime: { g: [0.03, 1.1, 1.45, 0.01, 0, 0.08], lo: [0.3, 0.3, 0.6], hi: [1, 0.85, 0.75], sharpen: 0.4, bloom: 0.15 },
    matte: { g: [0.02, 0.92, 0.85, 0, 0.07, 0] },
    cinematic: { g: [-0.01, 1.14, 0.94, -0.01, 0.03, 0.2], lo: [0.05, 0.35, 0.45], hi: [1, 0.78, 0.5], bloom: 0.08 },
    dream: { g: [0.05, 0.97, 1.16, 0.02, 0.045, 0.12], lo: [0.45, 0.3, 0.6], hi: [1, 0.8, 0.9], bloom: 0.55 },
    blood: { g: [-0.02, 1.2, 0.85, 0.03, 0, 0.34], lo: [0.3, 0.0, 0.05], hi: [1, 0.4, 0.32] },
    ice: { g: [0, 1.13, 0.78, -0.12, 0.02, 0.2], lo: [0.1, 0.25, 0.5], hi: [0.8, 0.95, 1] },
  };
  const GRADE = GRADES[E.grade] || GRADES.neutral;

  // ---------- face-follow (speech section) ----------
  function faceAt(t) {
    const F = E.faces;
    if (!F || !F.length) return null;
    // average over a short window: the camera eases after the face instead of jittering
    let x = 0, y = 0, s = 0, n = 0;
    for (let k = -3; k <= 3; k++) {
      const q = clamp(t + k * 0.1, F[0][0], F[F.length - 1][0]);
      let i = Math.min(F.length - 2, Math.max(0, Math.floor((q - F[0][0]) * 10)));
      while (i < F.length - 2 && F[i + 1][0] < q) i++;
      while (i > 0 && F[i][0] > q) i--;
      const a = F[i], b = F[i + 1] || a, u = b[0] > a[0] ? clamp((q - a[0]) / (b[0] - a[0])) : 0;
      const wgt = bell(k, 2.2);
      x += (a[1] + (b[1] - a[1]) * u) * wgt;
      y += (a[2] + (b[2] - a[2]) * u) * wgt;
      s += (a[3] + (b[3] - a[3]) * u) * wgt;
      n += wgt;
    }
    return { x: x / n, y: y / n, size: s / n };
  }

  function emphasisKick(t) {
    let k = 0;
    for (const line of E.captions) {
      if (t < line.start - 0.1 || t > line.end + 0.5) continue;
      for (const w of line.words) {
        if (!w.role) continue;
        const age = t - w.t;
        if (age >= 0 && age < 0.6) k = Math.max(k, Math.exp(-age / 0.16));
      }
    }
    return k;
  }

  // seconds since the last beat, and which beat it was
  function beatAge(t) {
    let age = 9, n = 0;
    for (let i = 0; i < E.beats.length; i++) { const a = t - E.beats[i]; if (a >= 0 && a < age) { age = a; n = i; } }
    return [age, n];
  }

  function beatPulse(t) {
    let p = 0;
    for (const b of E.beats) {
      const age = t - b;
      if (age >= 0 && age < 0.4) p = Math.max(p, Math.exp(-age / 0.09));
    }
    return p;
  }

  // ---------- camera ----------
  const blankCam = () => ({ zoom: 1.03, tx: 0, ty: 0, rot: 0, stretch: 1, flash: 0, mono: 0, ox: W / 2, oy: H / 2,
                            fx: W / 2, fy: H * 0.45,   // where the subject is: what depth effects focus on
                            invert: 0, glow: 0, rgb: 0, glitch: 0, vhs: 0, lens: 0, mirror: 0, edge: 0, poster: 0,
                            pixel: 0, leak: 0, impact: 0, outline: 0, aura: 0, echo: 0, isolate: 0,
                            waves: 0, warp: 0, tiles: 0, sweep: -1, reveal: 1, revealKind: 0, expo: 0, dim: 1 });

  function shotAt(t) {
    const frame = t * FPS;
    return E.shots.find(s => frame >= s.start && frame < s.end) || E.shots[E.shots.length - 1];
  }

  function camera(t) {
    const cam = blankCam();
    if (t < M) {
      cam.zoom += 0.035 * inOut(t / M);
      const inside = inOut((t - 0.05) / 0.55) * (1 - inOut((t - (M - 0.55)) / 0.5));
      const f = faceAt(t);
      if (f) {
        cam.fx = f.x; cam.fy = f.y;
        const tight = clamp(260 / Math.max(f.size, 60), 0.4, 1.4);
        cam.zoom += 0.13 * tight * inside;
        cam.tx = clamp((W / 2 - f.x) * 0.5, -130, 130) * inside;
        cam.ty = clamp((H * 0.42 - f.y) * 0.32, -45, 45) * inside;
      } else {
        cam.zoom += 0.08 * inside;
      }
      // a slow handheld drift keeps a locked-off speech shot alive
      cam.tx += 5 * smooth(t * 0.55, 11) * inside;
      cam.ty += 4 * smooth(t * 0.5, 23) * inside;
      const kick = emphasisKick(t);
      cam.zoom += 0.022 * kick;
      cam.flash = 0.05 * kick;
      return cam;
    }
    const frame = t * FPS;
    const shot = shotAt(t);
    const age = (frame - shot.start) / FPS;
    const dur = (shot.end - shot.start) / FPS;
    const left = dur - age;
    if (shot.face) { cam.ox = cam.fx = shot.face[0]; cam.oy = cam.fy = shot.face[1]; }
    const pulse = beatPulse(t) * L.pulse;
    const fx = shot.fx || [];

    if (shot.hero) {
      cam.mono = inOut(age / 0.3);
      cam.zoom = 1.06 + 0.08 * inOut(age / dur) + 0.035 * pulse + 0.12 * (1 - outExpo(age / 0.5));
      cam.flash = 0.75 * Math.exp(-age * 12);
      return shotFx(cam, fx, t, age, dur, pulse);
    }

    const hard = shot.move === 'cut';
    const [curve, settle] = EASES[shot.ease] || EASES.expo;
    const enter = hard ? 0 : 1 - curve(age / settle);
    const exit = hard ? 0 : Math.pow(1 - clamp(left / 0.14), 3);
    const kind = MOVES[shot.move] ?? shot.index % 4;
    const dir = shot.index % 8 < 4 ? 1 : -1;
    cam.zoom = 1.035 + 0.05 * inOut(age / dur) + 0.028 * pulse + 0.09 * exit;
    if (kind === 0) {            // punch in from a hard zoom
      cam.zoom += 0.24 * enter;
    } else if (kind === 1 || kind === 3) {  // vertical whip
      const d = kind === 1 ? dir : -dir;
      cam.ty = d * (190 * enter - 90 * exit);
      cam.stretch = 1 + 0.12 * enter + 0.1 * exit;
    } else if (kind === 2) {     // slide + roll
      cam.tx = dir * (150 * enter - 60 * exit);
      cam.rot = dir * 1.4 * enter;
      cam.zoom += 0.06 * enter;
    } else if (kind === 4) {     // spin in
      cam.rot = dir * 16 * enter;
      cam.zoom += 0.22 * enter;
    } else if (kind === 5) {     // start wide (the frame tiles past its edges) and settle in
      cam.zoom -= 0.22 * enter;
    } else if (kind === 7 || kind === 8) {   // straight slide in from the left or right edge
      cam.tx = (kind === 7 ? -1 : 1) * 560 * enter;
      cam.stretch = 1 + 0.03 * Math.abs(enter);
    } else if (kind === 9 || kind === 10) {  // straight slide up from the bottom, or down from the top
      cam.ty = (kind === 9 ? 1 : -1) * 340 * enter;
    } else if (kind === 11) {    // elastic: a big overshoot that rings out slowly
      cam.zoom += 0.3 * Math.exp(-age * 5) * Math.cos(age * 21);
      cam.rot = dir * 2.2 * Math.exp(-age * 5) * Math.sin(age * 21);
    } else {                     // bounce: overshoot and ring
      cam.zoom += 0.2 * Math.exp(-age * 9) * Math.cos(age * 30);
    }
    cam.flash = hard ? 0 : (kind === 0 ? 0.28 : 0.1) * Math.exp(-age * 16) + 0.06 * exit;
    return shotFx(cam, fx, t, age, dur, pulse);
  }

  function shotFx(cam, fx, t, age, dur, pulse) {
    const f = shotAt(t).tf;   // the layer's Transform: x, y, s(cale), r(otation), o(pacity); a key ending in 1 is where it ends up
    if (f) {
      const u = inOut(age / dur), at = (k, d) => { const a = f[k] ?? d, b = f[k + '1'] ?? a; return a + (b - a) * u; };
      cam.tx += at('x', 0); cam.ty += at('y', 0); cam.zoom *= at('s', 100) / 100; cam.rot += at('r', 0); cam.dim = at('o', 100) / 100;
    }
    let shake = L.shake * pulse * 14;                 // every beat kicks the camera
    if (fx.includes('shake')) shake += 11;            // handheld shake for the whole shot
    if (shake > 0.05) {
      cam.tx += shake * smooth(t * 13, 3);
      cam.ty += shake * smooth(t * 13, 71);
      cam.rot += shake * 0.035 * smooth(t * 11, 131);
    }
    if (fx.includes('wiggle')) {                      // a slow, loose drift, unlike the tight shake
      cam.tx += 30 * smooth(t * 2.2, 5); cam.ty += 20 * smooth(t * 2.0, 17);
      cam.rot += 1.3 * smooth(t * 1.7, 29); cam.zoom += 0.035;
    }
    if (fx.includes('waves')) cam.waves = 1;
    if (fx.includes('warp')) cam.warp = 0.55 + 0.9 * pulse;
    if (fx.includes('tiles')) cam.tiles = 1 - outExpo(age / 0.5);            // starts as a wall of tiles, lands on one
    if (fx.includes('sweep') && age < 0.85) cam.sweep = age / 0.85;         // a bar of light crosses the frame
    if (fx.includes('dissolve')) { cam.reveal = clamp(age / 0.5); cam.revealKind = 1; }   // bright parts arrive first
    if (fx.includes('cloud')) { cam.reveal = clamp(age / 0.7); cam.revealKind = 2; }      // clears out of white haze
    if (fx.includes('exposure')) cam.expo = 1.7 * Math.exp(-age * 7);       // blown out, then settles
    if (fx.includes('mono')) cam.mono = 1;
    if (fx.includes('strobe')) cam.flash = Math.max(cam.flash, 0.4 * pulse);
    if (fx.includes('push')) cam.zoom += 0.14 * inOut(age / dur);
    if (fx.includes('pull')) cam.zoom += 0.16 * (1 - inOut(age / dur));
    if (fx.includes('breathe')) cam.zoom += 0.07 * Math.sin(Math.PI * clamp(age / dur));
    if (fx.includes('invert')) cam.invert = 1;
    if (fx.includes('glow')) cam.glow = 1;
    if (fx.includes('rgb')) cam.rgb = 5 + 16 * pulse;        // colour split that kicks on the beat
    if (fx.includes('glitch')) cam.glitch = 0.45 + 0.55 * pulse;
    if (fx.includes('vhs')) cam.vhs = 1;
    if (fx.includes('lens')) cam.lens = 0.1 + 0.5 * pulse;   // fisheye bulge on the beat
    if (fx.includes('mirror')) cam.mirror = 1;
    if (fx.includes('edge')) cam.edge = 1;
    if (fx.includes('poster')) cam.poster = 1;
    if (fx.includes('pixel')) cam.pixel = 1;
    if (fx.includes('leak')) cam.leak = 1;
    if (fx.includes('impact') || fx.includes('twitch')) {
      const [age, n] = beatAge(t);
      // impact frames: two or three frames of pure black and white on the beat, flipped every other beat
      if (fx.includes('impact') && age < 0.075) cam.impact = n % 2 ? 2 : 1;
      if (fx.includes('twitch') && age < 0.2) {   // the frame jumps around for a moment after each beat
        const k = Math.floor(t * 30), w = 1 - age / 0.2;
        cam.tx += 46 * noise(k) * w; cam.ty += 30 * noise(k + 311) * w;
        cam.zoom += 0.09 * Math.abs(noise(k + 733)) * w; cam.rot += 1.6 * noise(k + 97) * w;
        cam.rgb = Math.max(cam.rgb, 16 * w);
      }
    }
    if (fx.includes('outline')) cam.outline = 1;
    if (fx.includes('aura')) cam.aura = 1;
    if (fx.includes('echo')) cam.echo = 1 + 1.2 * pulse;
    if (fx.includes('isolate')) cam.isolate = 1;
    return cam;
  }

  // ---------- WebGL footage pass ----------
  const glc = document.getElementById('gl');
  const gl = glc && glc.getContext('webgl2', { preserveDrawingBuffer: true, antialias: false, alpha: false });
  const TAPS = 12;
  const VERT = `#version 300 es
    in vec2 aPos; out vec2 vP;
    void main() { vP = vec2(aPos.x * 0.5 + 0.5, 0.5 - aPos.y * 0.5); gl_Position = vec4(aPos, 0.0, 1.0); }`;
  const HEAD = `#version 300 es
    precision highp float; in vec2 vP; out vec4 o;
    vec4 fb(sampler2D t, vec2 p) { return texture(t, vec2(p.x, 1.0 - p.y)); }
    float hash(vec2 p) { vec3 q = fract(vec3(p.xyx) * 0.1031); q += dot(q, q.yzx + 33.33); return fract((q.x + q.y) * q.z); }`;
  // camera: each tap is the frame under the camera at a slightly different moment -> real motion blur
  const CAMERA = HEAD + `
    uniform sampler2D uTex, uDepth; uniform vec3 uMx[${TAPS}]; uniform vec3 uMy[${TAPS}]; uniform int uN;
    uniform float uLens, uAspect, uHasDepth, uParZ; uniform vec2 uParT, uOrigin;
    void main() {
      vec2 p = vP;
      if (uLens != 0.0) { vec2 d = p - 0.5; d.x *= uAspect; d *= 1.0 - uLens * (0.5 - dot(d, d)); d.x /= uAspect; p = 0.5 + d; }
      vec3 q = vec3(p, 1.0), acc = vec3(0.0);
      float depth = 0.5;
      if (uHasDepth > 0.5) { int m = uN / 2; depth = texture(uDepth, vec2(dot(uMx[m], q), dot(uMy[m], q))).r; }
      for (int i = 0; i < ${TAPS}; i++) {
        if (i >= uN) break;
        vec2 s = vec2(dot(uMx[i], q), dot(uMy[i], q));
        // parallax: near things swell and slide more than far things as the camera moves
        if (uHasDepth > 0.5) s = uOrigin + (s - uOrigin) * (1.0 - uParZ * (depth - 0.5)) - uParT * (depth - 0.5);
        acc += texture(uTex, s).rgb;
      }
      o = vec4(acc / float(uN), depth);   // depth rides along in alpha, already under the camera
    }`;
  const BRIGHT = HEAD + `
    uniform sampler2D uTex; uniform vec2 uPx;
    void main() {
      vec3 c = (fb(uTex, vP + uPx * vec2(-1, -1)).rgb + fb(uTex, vP + uPx * vec2(1, -1)).rgb
              + fb(uTex, vP + uPx * vec2(-1, 1)).rgb + fb(uTex, vP + uPx * vec2(1, 1)).rgb) * 0.25;
      float l = max(c.r, max(c.g, c.b));
      o = vec4(c * smoothstep(0.55, 0.95, l), 1.0);
    }`;
  const BLUR = HEAD + `
    uniform sampler2D uTex; uniform vec2 uDir;
    void main() {
      vec3 c = fb(uTex, vP).rgb * 0.2270270;
      c += (fb(uTex, vP + uDir * 1.3846154).rgb + fb(uTex, vP - uDir * 1.3846154).rgb) * 0.3162162;
      c += (fb(uTex, vP + uDir * 3.2307692).rgb + fb(uTex, vP - uDir * 3.2307692).rgb) * 0.0702703;
      o = vec4(c, 1.0);
    }`;
  const FINISH = HEAD + `
    uniform sampler2D uTex, uBloom, uDepth, uSoft, uCap; uniform vec2 uRes, uFocus; uniform float uSeed, uT;
    uniform float uHasDepth, uDof, uPop, uCapOn, uBehind;
    uniform float uImpact, uOutline, uAura, uEcho, uIsolate, uDust, uFlicker, uBarAmt; uniform vec3 uAccent; uniform vec2 uSubject;
    float gFocus = 0.5;            // the subject's depth, set once per pixel in main
    float nearAt(vec2 q) { return smoothstep(gFocus * 0.6, gFocus * 0.85, fb(uTex, q).a); }
    uniform float uGrade[6]; uniform vec3 uLo, uHi;
    uniform float uBloomAmt, uGlow, uSharpen, uCA, uRGB, uGlitch, uVHS, uMirror, uEdge, uPoster, uPixel, uLeak;
    uniform float uMono, uInvert, uVig, uGrain, uFlash, uBars;
    uniform float uWaves, uWarp, uTiles, uSweep, uReveal, uRevealKind, uExpo, uDim;
    float vn(vec2 q) { vec2 i = floor(q), f = fract(q); f = f * f * (3.0 - 2.0 * f);
      return mix(mix(hash(i), hash(i + vec2(1, 0)), f.x), mix(hash(i + vec2(0, 1)), hash(i + vec2(1, 1)), f.x), f.y); }
    const vec3 LUMA = vec3(0.2126, 0.7152, 0.0722);
    vec3 pick(vec2 p) {            // the frame with lens colour fringing and the colour split
      vec2 d = (p - 0.5) * uCA * 0.006, s = vec2(uRGB / uRes.x, 0.0);
      return vec3(fb(uTex, p + d + s).r, fb(uTex, p).g, fb(uTex, p - d - s).b);
    }
    void main() {
      vec2 p = vP;
      if (uTiles > 0.001) { p = (p - 0.5) * (1.0 + 2.0 * uTiles) + 0.5; vec2 m = mod(p, 2.0); p = mix(m, 2.0 - m, step(1.0, m)); }
      if (uWaves > 0.5) { p.x += sin(p.y * 17.0 + uT * 5.0) * 0.007; p.y += sin(p.x * 13.0 + uT * 4.0) * 0.006; }
      if (uWarp > 0.0) p += (vec2(vn(p * 5.0 + uT * 1.3), vn(p * 5.0 + 7.3 - uT * 1.1)) - 0.5) * 0.045 * uWarp;
      if (uMirror > 0.5) p.x = 0.5 - abs(p.x - 0.5);
      if (uPixel > 0.5) { vec2 cell = vec2(22.0) / uRes; p = (floor(p / cell) + 0.5) * cell; }
      if (uGlitch > 0.0) {         // torn rows that jump every couple of frames
        float row = floor(p.y * 26.0), n = hash(vec2(row, uSeed));
        if (n > 1.0 - 0.3 * uGlitch) p.x += (hash(vec2(row + 7.0, uSeed)) - 0.5) * 0.16 * uGlitch;
        float band = floor(p.y * 7.0);
        if (hash(vec2(band, uSeed + 3.0)) > 0.93) p.y += (hash(vec2(band, uSeed + 9.0)) - 0.5) * 0.05 * uGlitch;
      }
      if (uVHS > 0.5) p.x += sin(p.y * 90.0 + uT * 14.0) * 0.0012 + (hash(vec2(floor(p.y * uRes.y / 3.0), uSeed)) - 0.5) * 0.0016;
      vec3 c = pick(p);
      if (uVHS > 0.5) {            // smeared chroma, scanlines, a rolling noise bar
        c.r = mix(c.r, fb(uTex, p + vec2(0.004, 0.0)).r, 0.6); c.b = mix(c.b, fb(uTex, p - vec2(0.004, 0.0)).b, 0.6);
        c *= 0.9 + 0.1 * sin(p.y * uRes.y * 3.14159);
        float bar = fract(p.y - uT * 0.22); c += smoothstep(0.03, 0.0, abs(bar - 0.5)) * hash(vec2(p.x * 90.0, uSeed)) * 0.22;
      }
      vec2 px = 1.0 / uRes;
      if (uSharpen > 0.0) {
        vec3 around = (fb(uTex, p + vec2(px.x, 0)).rgb + fb(uTex, p - vec2(px.x, 0)).rgb
                     + fb(uTex, p + vec2(0, px.y)).rgb + fb(uTex, p - vec2(0, px.y)).rgb) * 0.25;
        c += (c - around) * uSharpen;
      }
      if (uEdge > 0.5) {           // neon outlines
        float tl = dot(fb(uTex, p + px * vec2(-1.5, -1.5)).rgb, LUMA), tr = dot(fb(uTex, p + px * vec2(1.5, -1.5)).rgb, LUMA);
        float bl = dot(fb(uTex, p + px * vec2(-1.5, 1.5)).rgb, LUMA), br = dot(fb(uTex, p + px * vec2(1.5, 1.5)).rgb, LUMA);
        float e = smoothstep(0.04, 0.3, length(vec2(tl + bl - tr - br, tl + tr - bl - br)));
        c = c * 0.22 + e * mix(vec3(0.2, 0.9, 1.0), vec3(1.0, 0.25, 0.8), p.x + 0.3 * sin(uT * 2.0));
      }
      float near = 0.0;            // 1 on the subject and anything in front of it
      if (uHasDepth > 0.5) {
        // the subject's depth: a soft sample where the face is, never further back than the frame's average
        float d = fb(uTex, p).a, focus = max(textureLod(uDepth, uFocus, 4.0).r, textureLod(uDepth, vec2(0.5), 8.0).r);
        gFocus = focus;
        near = smoothstep(focus * 0.6, focus * 0.85, d);
        if (uEcho > 0.0 && near < 0.5) {   // ghost copies of the subject, growing outward behind them
          for (int k = 3; k >= 1; k--) {
            vec2 q = uSubject + (p - uSubject) / (1.0 + 0.1 * float(k) * uEcho);
            c = mix(c, fb(uTex, q).rgb, nearAt(q) * 0.66 / float(k));
          }
        }
        float coc = smoothstep(0.07, 0.42, focus - d) + 0.5 * smoothstep(0.2, 0.6, d - focus);   // behind blurs more than in front
        if (uDof > 0.0) c = mix(c, fb(uSoft, p).rgb, clamp(coc * uDof, 0.0, 1.0));
      }
      if (uHasDepth > 0.5 && uAura > 0.5) {   // light spilling out from behind the subject
        float halo = 0.0;
        for (int i = 0; i < 12; i++) {
          float a = float(i) * 0.5235988, r = i % 2 == 0 ? 16.0 : 40.0;
          halo += nearAt(p + vec2(cos(a), sin(a)) * r / uRes);
        }
        c += uAccent * clamp(halo / 12.0 * 1.7 - near * 1.7, 0.0, 1.0) * 0.9;
      }
      vec3 bloom = fb(uBloom, p).rgb;
      c += bloom * (uBloomAmt + uGlow * 0.9);
      c += bloom * vec3(1.0, 0.28, 0.12) * uBloomAmt * 0.35;   // halation: the red spill around highlights
      // grade: exposure, temperature, contrast, saturation, split tone, fade
      c *= 1.0 + uGrade[0] + uExpo;
      c.r *= 1.0 + uGrade[3]; c.b *= 1.0 - uGrade[3];
      c = (c - 0.5) * uGrade[1] + 0.5;
      float l = dot(c, LUMA);
      c = mix(vec3(l), c, uGrade[2]);
      vec3 tone = mix(uLo, uHi, smoothstep(0.1, 0.9, l));
      c += (tone - dot(tone, LUMA)) * uGrade[5] * (1.0 - 0.6 * abs(l - 0.5));
      c = c * (1.0 - uGrade[4]) + uGrade[4];
      if (uHasDepth > 0.5 && uPop > 0.0) c = mix(c, mix(vec3(dot(c, LUMA)), c, 0.55) * 0.5, (1.0 - near) * uPop);
      if (uHasDepth > 0.5 && uIsolate > 0.5) c = mix(vec3(dot(c, LUMA)) * 0.38, c, near);
      if (uHasDepth > 0.5 && uOutline > 0.5) {   // a cut-out stroke around the subject
        float hi = 0.0, lo = 1.0;
        for (int i = 0; i < 8; i++) {
          float a = float(i) * 0.7853982;
          float m = nearAt(p + vec2(cos(a), sin(a)) * 5.0 / uRes);
          hi = max(hi, m); lo = min(lo, m);
        }
        c = mix(c, vec3(1.0), smoothstep(0.3, 0.8, hi - lo));
      }
      if (uSweep >= 0.0) {         // light sweep
        float d = (vP.x + (vP.y - 0.5) * 0.4) - (uSweep * 1.7 - 0.35);
        c += vec3(1.0, 0.95, 0.86) * (smoothstep(0.16, 0.0, abs(d)) * 0.32 + smoothstep(0.035, 0.0, abs(d)) * 0.5) * (0.35 + dot(clamp(c, 0.0, 1.0), LUMA));
      }
      if (uRevealKind > 1.5) {     // cloud appear
        float n = (vn(vP * vec2(5.0, 3.0) + 3.0) + 0.5 * vn(vP * vec2(11.0, 7.0))) / 1.5, th = 1.2 * (1.0 - uReveal) - 0.2;
        c = mix(vec3(0.97, 0.96, 0.94), c, smoothstep(th, th + 0.2, n));
      } else if (uRevealKind > 0.5) {   // luma dissolve
        float th = 1.15 * (1.0 - uReveal) - 0.15;
        c *= smoothstep(th, th + 0.15, dot(clamp(c, 0.0, 1.0), LUMA));
      }
      if (uPoster > 0.5) c = floor(c * 5.0 + 0.5) / 5.0;
      c = mix(c, vec3(dot(c, LUMA)) * 1.12 - 0.04, uMono);
      if (uInvert > 0.5) c = 1.0 - c;
      if (uImpact > 0.5) { float ink = step(0.46, dot(c, LUMA)); c = (uImpact > 1.5 ? vec3(1.0 - ink) : vec3(ink)) * vec3(1.0, 0.97, 0.93); }
      c *= 1.0 + uFlicker * (hash(vec2(uSeed, 5.0)) - 0.5) * 0.3;
      if (uDust > 0.0) {           // slow motes of dust drifting through the light
        float motes = 0.0;
        for (int i = 0; i < 18; i++) {
          float f = float(i), sp = 0.012 + 0.03 * hash(vec2(f, 13.0));
          vec2 pos = fract(vec2(hash(vec2(f, 1.0)), hash(vec2(f, 7.0))) + vec2(uT * sp * 0.6 + 0.02 * sin(uT * 0.7 + f), -uT * sp));
          float size = 0.004 + 0.013 * hash(vec2(f, 21.0));
          motes += smoothstep(size, size * 0.25, length((vP - pos) * vec2(uRes.x / uRes.y, 1.0))) * (0.2 + 0.5 * hash(vec2(f, 29.0)));
        }
        c += vec3(1.0, 0.93, 0.8) * motes * uDust * 0.5;
      }
      if (uLeak > 0.5) {           // warm light leaking in from the edges
        float a = uT * 0.7;
        float k = smoothstep(0.9, 0.0, length((vP - vec2(0.12 + 0.2 * sin(a), 0.3 + 0.25 * cos(a * 1.3))) * vec2(1.0, 1.6)))
                + 0.7 * smoothstep(0.7, 0.0, length((vP - vec2(0.95 - 0.15 * cos(a * 0.8), 0.75)) * vec2(1.3, 1.0)));
        vec3 leak = vec3(1.0, 0.45, 0.15) * k * 0.55;
        c = 1.0 - (1.0 - clamp(c, 0.0, 1.0)) * (1.0 - leak);
      }
      vec2 v = (vP - vec2(0.5, 0.47)) * vec2(uRes.x / uRes.y, 1.0);
      c *= 1.0 - uVig * smoothstep(0.38, 1.1, length(v));
      if (uBars > 1.8) { float bar = max(0.0, (1.0 - (uRes.x / uRes.y) / uBars) * 0.5) * uBarAmt; if (vP.y < bar || vP.y > 1.0 - bar) c = vec3(0.0); }
      c *= uDim;
      c = mix(c, vec3(1.0, 0.988, 0.965), clamp(uFlash, 0.0, 1.0));
      c += (hash(vP * uRes + uSeed * 17.0) - 0.5) * uGrain * 2.4 * (1.0 - 0.55 * dot(clamp(c, 0.0, 1.0), LUMA));
      if (uCapOn > 0.5) {          // captions, hidden where the subject stands in front of them
        vec4 k = texture(uCap, vP);
        float show = 1.0 - near * uBehind;
        c = c * (1.0 - k.a * show) + k.rgb * show;
      }
      o = vec4(c, 1.0);
    }`;

  let G = null;   // programs, buffers and targets, or null when WebGL is not there
  function setupGL() {
    const compile = (type, src) => {
      const s = gl.createShader(type);
      gl.shaderSource(s, src); gl.compileShader(s);
      if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s));
      return s;
    };
    const vs = compile(gl.VERTEX_SHADER, VERT);
    const program = src => {
      const p = gl.createProgram();
      gl.attachShader(p, vs); gl.attachShader(p, compile(gl.FRAGMENT_SHADER, src));
      gl.bindAttribLocation(p, 0, 'aPos'); gl.linkProgram(p);
      if (!gl.getProgramParameter(p, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(p));
      const u = {};
      for (let i = 0; i < gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS); i++) {
        const name = gl.getActiveUniform(p, i).name.replace('[0]', '');
        u[name] = gl.getUniformLocation(p, name);
      }
      return { p, u };
    };
    const target = (w, h) => {
      const tex = gl.createTexture();
      gl.bindTexture(gl.TEXTURE_2D, tex);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, w, h, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      const fbo = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
      return { tex, fbo, w, h };
    };
    const quad = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, quad);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    gl.enableVertexAttribArray(0);
    gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
    const src = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, src);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    // past the frame's edge the picture mirrors, so big whips and zoom-outs never show black
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.MIRRORED_REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.MIRRORED_REPEAT);
    const depth = gl.createTexture();   // mipmapped, so one blurry sample gives the subject's depth
    gl.bindTexture(gl.TEXTURE_2D, depth);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR_MIPMAP_LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.MIRRORED_REPEAT);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.MIRRORED_REPEAT);
    const cap = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, cap);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    return { src, depth, cap, camera: program(CAMERA), bright: program(BRIGHT), blur: program(BLUR), finish: program(FINISH),
             full: target(W, H), half: target(W / 2, H / 2), q1: target(W / 4, H / 4), q2: target(W / 4, H / 4),
             s1: target(W / 2, H / 2), s2: target(W / 4, H / 4), s3: target(W / 4, H / 4) };
  }
  if (gl) {
    try { G = setupGL(); } catch (err) { console.error('easyedit shaders: ' + err.message); }
  }
  if (G) video.style.opacity = '0';   // still laid out (the renderer needs it), but the shader pass is what shows

  // HyperFrames swaps in an exact still for each rendered frame; in the editor it is the playing video
  function frameSource(el) {
    const still = el.nextElementSibling;
    if (still && still.classList && still.classList.contains('__render_frame__') && still.complete && still.naturalWidth > 0) return still;
    return el.readyState >= 2 ? el : null;
  }
  const capCanvas = document.createElement('canvas');
  capCanvas.width = W; capCanvas.height = H;
  const capCtx = capCanvas.getContext('2d');
  let capReady = false;   // captions were drawn into capCanvas for this frame

  // out px -> source uv for one camera, as two rows of a 2x3 matrix
  function inverse(cam, mx, my, i) {
    const a = cam.rot * Math.PI / 180, c = Math.cos(a), s = Math.sin(a);
    const zx = cam.zoom, zy = cam.zoom * cam.stretch, px = cam.ox + cam.tx, py = cam.oy + cam.ty;
    mx[i * 3] = c / zx;  mx[i * 3 + 1] = (s / zx) * H / W;  mx[i * 3 + 2] = (cam.ox - (c * px + s * py) / zx) / W;
    my[i * 3] = (-s / zy) * W / H;  my[i * 3 + 1] = c / zy;  my[i * 3 + 2] = (cam.oy - (-s * px + c * py) / zy) / H;
  }

  const MX = new Float32Array(TAPS * 3), MY = new Float32Array(TAPS * 3);
  function pass(prog, to, w, h) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, to);
    gl.viewport(0, 0, w, h);
    gl.useProgram(prog.p);
  }
  const bind = (unit, tex) => { gl.activeTexture(gl.TEXTURE0 + unit); gl.bindTexture(gl.TEXTURE_2D, tex); };

  function film(t, cam) {
    const source = frameSource(video);
    if (!G || !source) return;
    bind(0, G.src);
    try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source); } catch (err) { return; }
    const depthSource = depthVideo && frameSource(depthVideo);
    let hasDepth = 0;
    if (depthSource) {
      bind(2, G.depth);
      try { gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, depthSource); gl.generateMipmap(gl.TEXTURE_2D); hasDepth = 1; } catch (err) { /* no depth this frame */ }
    }

    // the shutter stays open for a slice of time; sample the camera across it, inside this shot only
    const shutter = L.blur / 30;
    const lo = t < M ? 0 : shotAt(t).start / FPS, hi = (t < M ? M : shotAt(t).end / FPS) - 1e-4;
    let n = TAPS;
    for (let i = 0; i < TAPS; i++) inverse(camera(clamp(t + (i / (TAPS - 1) - 0.5) * shutter, lo, hi)), MX, MY, i);
    const last = (TAPS - 1) * 3;
    const moved = Math.abs(MX[2] - MX[last + 2]) * W + Math.abs(MY[2] - MY[last + 2]) * H
                + (Math.abs(MX[0] - MX[last]) + Math.abs(MX[1] - MX[last + 1])) * W;
    if (shutter <= 0 || moved < 0.6) { inverse(cam, MX, MY, 0); n = 1; }   // a still camera needs one tap

    let u = G.camera.u;
    pass(G.camera, G.full.fbo, W, H);
    gl.uniform1i(u.uTex, 0); gl.uniform3fv(u.uMx, MX); gl.uniform3fv(u.uMy, MY); gl.uniform1i(u.uN, n);
    gl.uniform1f(u.uLens, cam.lens); gl.uniform1f(u.uAspect, W / H);
    gl.uniform1i(u.uDepth, 2); gl.uniform1f(u.uHasDepth, hasDepth);
    gl.uniform1f(u.uParZ, L.parallax * 0.6 * (cam.zoom - 1)); gl.uniform2f(u.uParT, L.parallax * 0.5 * cam.tx / W, L.parallax * 0.5 * cam.ty / H);
    gl.uniform2f(u.uOrigin, cam.ox / W, cam.oy / H);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);

    // bloom: bright parts, shrunk and blurred twice, wide then wider
    pass(G.bright, G.half.fbo, W / 2, H / 2);
    bind(0, G.full.tex); gl.uniform1i(G.bright.u.uTex, 0); gl.uniform2f(G.bright.u.uPx, 1 / W, 1 / H);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    u = G.blur.u;
    const blurTo = (from, to, dx, dy) => {
      pass(G.blur, to.fbo, to.w, to.h);
      bind(0, from.tex); gl.uniform1i(u.uTex, 0); gl.uniform2f(u.uDir, dx / to.w, dy / to.h);
      gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    };
    blurTo(G.half, G.q1, 1.2, 0); blurTo(G.q1, G.q2, 0, 1.2); blurTo(G.q2, G.q1, 2.6, 0); blurTo(G.q1, G.q2, 0, 2.6);
    const dof = hasDepth && L.dof > 0;
    if (dof) {   // a soft copy of the whole frame for whatever is out of focus
      blurTo(G.full, G.s1, 0, 0); blurTo(G.s1, G.s2, 1.5, 0); blurTo(G.s2, G.s3, 0, 1.5); blurTo(G.s3, G.s2, 3.2, 0); blurTo(G.s2, G.s3, 0, 3.2);
    }
    const caps = capReady ? 1 : 0;
    if (caps) {
      bind(3, G.cap);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, true);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, capCanvas);
      gl.pixelStorei(gl.UNPACK_PREMULTIPLY_ALPHA_WEBGL, false);
    }

    u = G.finish.u;
    pass(G.finish, null, W, H);
    bind(0, G.full.tex); bind(1, G.q2.tex); bind(4, G.s3.tex);
    gl.uniform1i(u.uTex, 0); gl.uniform1i(u.uBloom, 1); gl.uniform2f(u.uRes, W, H);
    gl.uniform1i(u.uDepth, 2); gl.uniform1i(u.uCap, 3); gl.uniform1i(u.uSoft, 4);
    gl.uniform1f(u.uHasDepth, hasDepth); gl.uniform1f(u.uDof, dof ? L.dof : 0); gl.uniform1f(u.uPop, L.pop);
    gl.uniform2f(u.uFocus, cam.fx / W, cam.fy / H); gl.uniform1f(u.uCapOn, caps); gl.uniform1f(u.uBehind, hasDepth);
    gl.uniform1f(u.uSeed, Math.floor(t * FPS / 2) % 997); gl.uniform1f(u.uT, t);
    const GR = (t >= M && GRADES[shotAt(t).grade]) || GRADE;   // a shot can carry its own colour correction
    gl.uniform1fv(u.uGrade, GR.g);
    gl.uniform3fv(u.uLo, GR.lo || [0.5, 0.5, 0.5]); gl.uniform3fv(u.uHi, GR.hi || [0.5, 0.5, 0.5]);
    gl.uniform1f(u.uBloomAmt, L.bloom + (GR.bloom || 0)); gl.uniform1f(u.uGlow, cam.glow);
    gl.uniform1f(u.uSharpen, L.sharpen + (GR.sharpen || 0)); gl.uniform1f(u.uCA, L.lens);
    gl.uniform1f(u.uWaves, cam.waves); gl.uniform1f(u.uWarp, cam.warp); gl.uniform1f(u.uTiles, cam.tiles);
    gl.uniform1f(u.uSweep, cam.sweep); gl.uniform1f(u.uReveal, cam.reveal); gl.uniform1f(u.uRevealKind, cam.revealKind);
    gl.uniform1f(u.uExpo, cam.expo); gl.uniform1f(u.uDim, cam.dim);
    gl.uniform1f(u.uRGB, cam.rgb); gl.uniform1f(u.uGlitch, cam.glitch); gl.uniform1f(u.uVHS, cam.vhs);
    gl.uniform1f(u.uMirror, cam.mirror); gl.uniform1f(u.uEdge, cam.edge); gl.uniform1f(u.uPoster, cam.poster);
    gl.uniform1f(u.uPixel, cam.pixel); gl.uniform1f(u.uLeak, cam.leak);
    gl.uniform1f(u.uMono, cam.mono); gl.uniform1f(u.uInvert, cam.invert);
    gl.uniform1f(u.uVig, L.vignette); gl.uniform1f(u.uGrain, L.grain); gl.uniform1f(u.uFlash, cam.flash * L.flash);
    gl.uniform1f(u.uBars, L.bars);
    gl.uniform1f(u.uBarAmt, L.barsDrop ? outExpo((t - M) / 0.4) : 1);   // bars can slam in on the drop
    gl.uniform1f(u.uImpact, cam.impact); gl.uniform1f(u.uOutline, cam.outline); gl.uniform1f(u.uAura, cam.aura);
    gl.uniform1f(u.uEcho, cam.echo); gl.uniform1f(u.uIsolate, cam.isolate);
    gl.uniform1f(u.uDust, L.dust); gl.uniform1f(u.uFlicker, L.flicker); gl.uniform3fv(u.uAccent, ACCENT);
    // where the subject lands on screen once the camera has moved the frame
    const ang = cam.rot * Math.PI / 180, dx = (cam.fx - cam.ox) * cam.zoom, dy = (cam.fy - cam.oy) * cam.zoom * cam.stretch;
    gl.uniform2f(u.uSubject, (cam.ox + cam.tx + Math.cos(ang) * dx - Math.sin(ang) * dy) / W,
                 (cam.oy + cam.ty + Math.sin(ang) * dx + Math.cos(ang) * dy) / H);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  // ---------- captions ----------
  const CAP_Y = H * L.capY;
  const SIZE = L.capSize;
  const sans = (px, weight) => `${weight} ${px}px EECaption`;
  // text styles: the font, the letter case and how each word is painted
  //   plain: no outline-first reveal    mode: a painter of its own (see paintWord)
  //   font, case and track can depend on the word, so emphasis words can look different
  const TEXT_STYLES = {
    clean: { font: () => L.capFont === 'display' ? `${SIZE}px EEDisplay` : sans(SIZE, 800) },
    serif: { font: () => `italic 500 ${SIZE * 1.12}px EESerif`, case: 'lower', track: 0, glow: 0.5, plain: true },
    mixed: { font: w => w.role ? `italic 600 ${SIZE * 1.3}px EESerif` : sans(SIZE * 0.56, 500), case: w => w.role ? 'lower' : 'upper',
             track: w => w.role ? 0 : 7, glow: 0.5, plain: true, gap: 0.8 },
    aesthetic: { font: () => sans(SIZE * 0.6, 300), case: 'lower', track: 11, glow: 0.3, plain: true, gap: 0.9 },
    trailer: { font: () => `500 ${SIZE * 0.72}px EETrailer`, case: 'upper', track: 15, glow: 0.4, plain: true, gap: 1.2 },
    script: { font: () => `600 ${SIZE * 1.3}px EEScript`, case: 'lower', track: 0, glow: 0.5, plain: true, gap: 0.9 },
    marker: { font: () => `${SIZE * 1.05}px EEMarker`, track: 0, glow: 0.35, plain: true, tilt: 3 },
    subtitle: { font: () => `600 ${SIZE * 0.78}px Arial, Helvetica, sans-serif`, case: 'sentence', track: 0, mode: 'subtitle', base: '#ffe14d', gap: 0.85 },
    typewriter: { font: () => `${SIZE * 0.8}px EEType`, track: 1, mode: 'type', still: true },
    outline: { font: () => `${SIZE * 1.15}px EEDisplay`, case: 'upper', track: 3, mode: 'hollow' },
    neon: { font: () => sans(SIZE, 600), track: 4, mode: 'neon', base: P.cool || '#7de3ff' },
    boxed: { font: () => sans(SIZE * 0.78, 800), track: 0, mode: 'box', gap: 2.7 },
    karaoke: { font: () => sans(SIZE * 0.95, 800), mode: 'karaoke' },
    impact: { font: () => `${SIZE * 1.3}px EEDisplay`, case: 'upper', track: 1, mode: 'impact', tilt: 2.5, gap: 1.25 },
    glitch: { font: () => sans(SIZE, 800), mode: 'rgb' },
    vhs: { font: () => `${SIZE * 1.2}px EEVhs`, case: 'upper', track: 3, mode: 'rgb', still: true },
  };
  const ST = TEXT_STYLES[L.capStyle] || TEXT_STYLES.clean;
  const per = (v, w) => typeof v === 'function' ? v(w) : v;
  function wordLook(w, first) {
    const kase = per(ST.case, w);
    let text = w.text;
    if (kase === 'lower') text = text.toLowerCase();
    else if (kase === 'upper') text = text.toUpperCase();
    else if (kase === 'sentence') {
      text = text.toLowerCase();
      if (first || /^i(['’]|$)/.test(text)) text = text.charAt(0).toUpperCase() + text.slice(1);
    }
    const font = ST.font(w);
    return { text, font, px: parseFloat(font.match(/([\d.]+)px/)[1]), track: (per(ST.track, w) ?? 2) + (L.capTrack - 2) };
  }

  // one word, already moved into place: (0, 0) is its centre
  function paintWord(k, w, i, t, age, alpha, fill, arrive, color, width, waiting) {
    const mode = ST.mode, px = k.px;
    if (mode === 'subtitle') {            // film subtitle: flat colour with a solid black edge
      ctx.globalAlpha = alpha;
      ctx.lineJoin = 'round'; ctx.lineWidth = px * 0.17; ctx.strokeStyle = '#000';
      ctx.strokeText(k.text, 0, 0);
      ctx.fillStyle = color;
      ctx.fillText(k.text, 0, 0);
    } else if (mode === 'type') {         // typewriter: one letter at a time
      const n = Math.max(1, Math.floor((age + 0.05) / 0.035));
      ctx.globalAlpha = alpha;
      ctx.textAlign = 'left';
      ctx.shadowColor = 'rgba(0,0,0,0.7)'; ctx.shadowBlur = 10;
      ctx.fillStyle = color;
      ctx.fillText(k.text.slice(0, n), -width / 2, 0);
    } else if (mode === 'hollow') {       // outline only; the words that matter are filled in
      ctx.globalAlpha = alpha;
      ctx.shadowColor = 'rgba(0,0,0,0.6)'; ctx.shadowBlur = 12;
      ctx.lineJoin = 'round'; ctx.lineWidth = Math.max(1.6, px * 0.03); ctx.strokeStyle = color;
      ctx.strokeText(k.text, 0, 0);
      if (w.role) { ctx.globalAlpha = alpha * fill; ctx.shadowColor = color; ctx.shadowBlur = 22 * L.capGlow; ctx.fillStyle = color; ctx.fillText(k.text, 0, 0); }
    } else if (mode === 'neon') {         // a lit tube: white-hot core, coloured light around it
      const flick = age < 0.35 && noise(Math.floor(t * 30) + i * 7) > 0.35 ? 0.35 : 1;   // it stutters on
      ctx.globalAlpha = alpha * flick;
      ctx.shadowColor = color;
      ctx.shadowBlur = 34 * Math.max(0.4, L.capGlow); ctx.fillStyle = color; ctx.fillText(k.text, 0, 0);
      ctx.shadowBlur = 12 * Math.max(0.4, L.capGlow); ctx.fillStyle = '#ffffff'; ctx.fillText(k.text, 0, 0);
    } else if (mode === 'box') {          // each word on its own block
      const padX = px * 0.3, h = px * 1.28;
      ctx.globalAlpha = alpha;
      ctx.shadowColor = 'rgba(0,0,0,0.45)'; ctx.shadowBlur = 16;
      ctx.fillStyle = w.role ? color : P.white;
      ctx.beginPath(); ctx.roundRect(-width / 2 - padX, -h / 2, width + padX * 2, h, px * 0.16); ctx.fill();
      ctx.shadowColor = 'transparent';
      ctx.fillStyle = '#101114';
      ctx.fillText(k.text, 0, px * 0.04);
    } else if (mode === 'karaoke') {      // the whole line waits, dim; each word lights up as it is said
      ctx.shadowColor = 'rgba(0,0,0,0.6)'; ctx.shadowBlur = 14;
      ctx.globalAlpha = alpha * (waiting ? 0.34 : 1);
      ctx.fillStyle = waiting ? P.white : color;
      if (!waiting) { ctx.shadowColor = color; ctx.shadowBlur = 18 * L.capGlow; }
      ctx.fillText(k.text, 0, 0);
    } else if (mode === 'impact') {       // heavy block letters with a hard edge and a drop shadow
      ctx.globalAlpha = alpha;
      ctx.lineJoin = 'round'; ctx.lineWidth = px * 0.15; ctx.strokeStyle = '#000';
      ctx.shadowColor = 'rgba(0,0,0,0.9)'; ctx.shadowOffsetY = px * 0.07; ctx.shadowBlur = 0;
      ctx.strokeText(k.text, 0, 0);
      ctx.shadowColor = 'transparent';
      ctx.fillStyle = color;
      ctx.fillText(k.text, 0, 0);
    } else if (mode === 'rgb') {          // colour-split text; the split is wide as the word lands
      const split = 3 + 16 * (1 - fill) + (ST.still ? 0 : 1.5 * noise(Math.floor(t * 24) + i * 13));
      ctx.globalAlpha = alpha;
      ctx.globalCompositeOperation = 'lighter';
      ctx.fillStyle = '#ff2a4d'; ctx.fillText(k.text, -split, 0);
      ctx.fillStyle = '#19e6ff'; ctx.fillText(k.text, split, 0);
      ctx.globalCompositeOperation = 'source-over';
      ctx.shadowColor = 'rgba(0,0,0,0.5)'; ctx.shadowBlur = 8;
      ctx.fillStyle = color;
      ctx.fillText(k.text, 0, 0);
    } else if (ST.plain) {                // soft, quiet styles: a dark seat, then the letters with a little glow
      ctx.globalAlpha = alpha;
      ctx.shadowColor = 'rgba(0,0,0,0.6)'; ctx.shadowBlur = 16;
      ctx.fillStyle = color;
      ctx.fillText(k.text, 0, 0);
      ctx.shadowColor = color; ctx.shadowBlur = (w.role ? 24 : 10) * L.capGlow * (ST.glow ?? 1);
      ctx.fillText(k.text, 0, 0);
    } else {                              // the easyedit look: outline first, then fill with glow
      ctx.shadowColor = 'rgba(0,0,0,0.55)';
      ctx.shadowBlur = 14;
      ctx.globalAlpha = alpha * (0.25 + 0.75 * (1 - fill)) * 0.9;
      ctx.lineWidth = 1.1;
      ctx.strokeStyle = color;
      ctx.strokeText(k.text, 0, 0);
      ctx.globalAlpha = alpha * fill;
      ctx.shadowColor = color;
      ctx.shadowBlur = (w.role ? 26 : 12) * L.capGlow;
      ctx.fillStyle = color;
      ctx.fillText(k.text, 0, 0);
    }
  }

  // ---------- signature captions ----------
  // Every letter is animated on its own, then the whole caption goes through a small compositor:
  // a sharp layer, a defocused layer letters fade through as they arrive, a bloom built from
  // shrunken copies (no canvas blur filter: that is the slow path), a soft seat shadow and colour fringes.
  const GOLD = [[0, '#fff7d6'], [0.32, '#f5cf72'], [0.52, '#b47f24'], [0.66, '#ffe9a8'], [1, '#8f5f17']];
  const CHROME = [[0, '#ffffff'], [0.42, '#bcd8f5'], [0.5, '#4e5f78'], [0.53, '#141922'], [0.78, '#aab8c9'], [1, '#f4f8ff']];
  const PRO = {
    cinema: { look: w => ({ px: SIZE * 0.82, font: px => sans(px, w.role ? 800 : 600), kase: 'upper', track: 0.14 }),
              reveal: 'blur', stagger: 0.028, dur: 0.5, glow: 1, chroma: 1.5, rule: true },
    lyric: { look: w => ({ px: SIZE * (w.role ? 1.42 : 1.14), font: px => `italic ${w.role ? 600 : 500} ${px}px EESerif`, kase: 'lower', track: 0 }),
             reveal: 'blur', rise: 0.3, stagger: 0.03, dur: 0.55, glow: 0.8, gap: 0.26 },
    kinetic: { layout: 'block', look: w => ({ px: SIZE, font: px => w.role ? `${px}px EEDisplay` : sans(px, 800), kase: 'upper', track: 0.01 }),
               reveal: 'rise', stagger: 0.012, dur: 0.5, glow: 0.3, seat: 0.8, leading: 0.98 },
    duo: { layout: 'duo', look: (w, big) => big ? { px: SIZE * 1.6, font: px => `italic 600 ${px}px EESerif`, kase: 'lower', track: 0 }
                                               : { px: SIZE * 0.4, font: px => sans(px, 500), kase: 'upper', track: 0.32 },
           reveal: 'blur', rise: 0.2, stagger: 0.03, dur: 0.5, glow: 0.7, leading: 1.14 },
    gold: { look: w => ({ px: SIZE * (w.role ? 1.06 : 0.86), font: px => `800 ${px}px EETrailer`, kase: 'upper', track: 0.2 }),
            reveal: 'blur', stagger: 0.035, dur: 0.6, glow: 0.6, fill: GOLD, shimmer: 1, seat: 1, hot: 0.12, rule: true },
    chrome: { look: () => ({ px: SIZE * 1.3, font: px => `${px}px EEDisplay`, kase: 'upper', track: 0.03 }),
              reveal: 'slam', stagger: 0, dur: 0.38, glow: 0.3, fill: CHROME, stroke: 0.05, shimmer: 1, skew: 0.14, seat: 1 },
    wide: { look: w => ({ px: SIZE * 0.62, font: px => sans(px, w.role ? 600 : 300), kase: 'upper', track: 0.36 }),
            reveal: 'track', stagger: 0, dur: 0.6, glow: 0.6, gap: 0.45 },
    ghost: { look: () => ({ px: SIZE, font: px => sans(px, 800), kase: 'upper', track: 0.03 }),
             reveal: 'trail', stagger: 0.02, dur: 0.5, glow: 0.9 },
    whisper: { look: w => ({ px: SIZE * 0.6, font: px => sans(px, w.role ? 600 : 400), kase: 'lower', track: 0.3 }),
               reveal: 'blur', stagger: 0.04, dur: 0.8, glow: 1.7, hot: 0.35, drift: 5, gap: 0.35, seat: 0.35 },
    hype: { look: () => ({ px: SIZE * 1.5, font: px => `${px}px EEDisplay`, kase: 'upper', track: 0.02 }),
            reveal: 'slam', stagger: 0, dur: 0.3, glow: 0.45, chroma: 4, skew: 0.16, seat: 1, stroke: 0.035 },
    neonscript: { look: () => ({ px: SIZE * 1.4, font: px => `700 ${px}px EEScript`, kase: 'lower', track: 0 }),
                  reveal: 'blur', stagger: 0.022, dur: 0.4, glow: 2.3, hot: 0.75, base: P.cool || '#7de3ff', flicker: true, gap: 0.24, seat: 0.3 },
  };
  // Text animations in the After Effects manner: a text animator (offset, rotation, tracking, blur,
  // opacity 0) driven by a selector. Two kinds of selector, both rebuilt here:
  //   expression: amount = a * cos(2*pi*f*t) / exp(d*t), each word or letter starting `delay` after the last
  //   sweep: a ramp crosses the word in `sweep` seconds, each letter easing home as it passes
  // Values are in pixels at a 72px caption and scale with the caption size.
  const MOTIONS = {
    'ease-up': { unit: 'word', a: 70, f: 0, d: 6, y: 300 },
    'ease-down': { unit: 'word', a: 70, f: 0, d: 6, y: -300 },
    'blur-up': { unit: 'word', a: 300, f: 0, d: 5, y: 30, blur: 25 },
    'blur-down': { unit: 'word', a: 300, f: 0, d: 5, y: -30, blur: 25 },
    'pop-up': { unit: 'word', a: 10, f: 0, d: 5, y: 400 },
    'pop-down': { unit: 'word', a: 10, f: 0, d: 5, y: -400 },
    'bounce-up': { unit: 'word', a: 35, f: 2.6, d: 5, y: 500, rot: 50 },
    'bounce-down': { unit: 'word', a: 35, f: 2.6, d: 5, y: -500, rot: -50 },
    'wobble-up': { unit: 'word', a: 100, f: 1.8, d: 5, y: 350, track: 20 },
    'wobble-down': { unit: 'word', a: 100, f: 1.8, d: 5, y: -350, track: -20 },
    'slide-up': { unit: 'char', delay: 0.02, a: 80, f: 0, d: 7, y: 150, blur: 25 },
    'slide-down': { unit: 'char', delay: 0.02, a: 80, f: 0, d: 7, y: -150, blur: 25 },
    'random-up': { unit: 'char', delay: 0.02, random: true, a: 80, f: 0, d: 7, y: 50, blur: 25 },
    'random-down': { unit: 'char', delay: 0.02, random: true, a: 80, f: 0, d: 7, y: -50, blur: 25 },
    'snappy-up': { unit: 'char', sweep: 0.767, ease: 'snap', y: 200, rot: -35, blur: 20 },
    'snappy-down': { unit: 'char', sweep: 0.767, ease: 'snap', y: -200, rot: 35, blur: 20 },
    'reveal-right': { unit: 'char', sweep: 0.767, ease: 'soft', x: -50, blur: 20 },
    'reveal-left': { unit: 'char', sweep: 0.767, ease: 'soft', reverse: true, x: 50, track: -40, blur: 20 },
    merge: { unit: 'char', sweep: 1, ease: 'merge', random: true, y: 500, rot: -35, track: 150, blur: 20 },
    'left-slide': { unit: 'word', a: 100, f: 0, d: 6, x: -420, blur: 10 },
    'right-slide': { unit: 'word', a: 100, f: 0, d: 6, x: 420, blur: 10 },
    'top-slide': { unit: 'word', a: 100, f: 0, d: 6, y: -260, blur: 10 },
    'bottom-slide': { unit: 'word', a: 100, f: 0, d: 6, y: 260, blur: 10 },
    blink: { unit: 'char', sweep: 0.3, ease: 'step', random: true },
  };
  const MOT = MOTIONS[L.capMotion] || null;
  // text effects that sit on top of any signature style
  const TFX = new Set(L.capFx || []);
  let cellTex = null, cloudMasks = null;
  function buildCells() {   // a field of cells, bright along the walls, for the letters to be cut from
    const n = 256, c = document.createElement('canvas');
    c.width = c.height = n;
    const g = c.getContext('2d'), img = g.createImageData(n, n), pts = [];
    for (let i = 0; i < 26; i++) pts.push([(noise(i * 3.1) * 0.5 + 0.5) * n, (noise(i * 7.7 + 5) * 0.5 + 0.5) * n]);
    for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) {
      let a = 1e9, b = 1e9;
      for (const [px, py] of pts) {   // wraps at the edges, so the texture tiles
        const dx = Math.min(Math.abs(x - px), n - Math.abs(x - px)), dy = Math.min(Math.abs(y - py), n - Math.abs(y - py)), d = dx * dx + dy * dy;
        if (d < a) { b = a; a = d; } else if (d < b) b = d;
      }
      const wall = 1 - clamp((Math.sqrt(b) - Math.sqrt(a)) / 9), v = 40 + 215 * wall, i = (y * n + x) * 4;
      img.data[i] = v; img.data[i + 1] = v; img.data[i + 2] = Math.min(255, v + 20); img.data[i + 3] = 255;
    }
    g.putImageData(img, 0, 0);
    return c;
  }
  function buildClouds() {   // the same soft noise cut at 24 levels: stepping through them is a cloud clearing
    const w = 256, h = 96, vals = new Float32Array(w * h);
    const vn = (x, y) => { const i = Math.floor(x), j = Math.floor(y), f = x - i, k = y - j, u = f * f * (3 - 2 * f), v = k * k * (3 - 2 * k);
      const r = (a, b) => noise(a * 57 + b * 131) * 0.5 + 0.5;
      return (r(i, j) * (1 - u) + r(i + 1, j) * u) * (1 - v) + (r(i, j + 1) * (1 - u) + r(i + 1, j + 1) * u) * v; };
    for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) vals[y * w + x] = (vn(x / 30, y / 22) + 0.5 * vn(x / 13, y / 10) + 0.25 * vn(x / 6, y / 5)) / 1.75;
    return Array.from({ length: 24 }, (_, k) => {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const g = c.getContext('2d'), img = g.createImageData(w, h), level = (k + 1) / 24 * 1.25 - 0.1;
      for (let i = 0; i < w * h; i++) { img.data[i * 4 + 3] = 255 * clamp((level - vals[i]) / 0.14); }
      g.putImageData(img, 0, 0);
      return c;
    });
  }
  // where one letter is at `age` seconds after its word was said; null while it is still hidden
  function motionAt(M, it, ci, n, age) {
    if (age < 0) return null;
    const k = SIZE / 72;
    let s;
    if (M.sweep) {
      const order = M.random ? it.order[ci] : M.reverse ? n - 1 - ci : ci;
      const T = M.sweep * (0.55 + 0.45 * Math.min(1, n / 8));   // short words do not need the whole sweep
      if (M.ease === 'step') s = age >= (order + 1) / n * T ? 0 : 1;
      else if (M.ease === 'merge') s = Math.pow(clamp((order + 0.5) / n - age / T) / Math.max(0.05, (order + 0.5) / n), 2) * ((order + 0.5) / n);
      else s = Math.pow(1 - clamp(age / (T / 2) - order / n), M.ease === 'snap' ? 4 : 2.2);
    } else {
      const t = age - (M.unit === 'char' ? (M.random ? it.order[ci] : ci) * M.delay : 0);
      if (t < 0) return null;
      s = M.a / 100 * Math.cos(M.f * t * 2 * Math.PI) / Math.exp(M.d * t);
    }
    const seen = 1 - clamp(s);   // the animator's opacity is 0, so the amount is also how hidden the letter is
    if (seen <= 0.004) return null;
    const blur = (M.blur || 0) * Math.abs(s), far = clamp((blur - 8) / 16), near = clamp(blur / 5) * (1 - far);
    return { dx: ((M.x || 0) + (ci - (n - 1) / 2) * (M.track || 0)) * k * s, dy: (M.y || 0) * k * s, rot: (M.rot || 0) * s,
             sharp: seen * (1 - clamp(blur / 5)), soft: seen * near, heavy: seen * far };
  }

  let proS = null, proF = null, proH = null, proPyr = null, proTint = null, proSoft = null, proHeavy = null, proTmp = null;
  function proSetup() {
    const mk = (w, h) => { const c = document.createElement('canvas'); c.width = Math.ceil(w); c.height = Math.ceil(h); return c; };
    proS = mk(W, H); proF = mk(W, H); proH = mk(W, H);   // sharp, out of focus, far out of focus
    proPyr = [2, 4, 8, 16].map(d => mk(W / d, H / d));
    proTint = mk(W / 2, H / 2); proSoft = mk(W / 4, H / 4); proHeavy = mk(W / 8, H / 8);
    if (TFX.has('turb')) proTmp = mk(W, H);
  }
  const proCache = new WeakMap();
  function proLayout(line, st) {
    const key = line.words.map(w => w.text + '/' + (w.role || '')).join('|');
    const hit = proCache.get(line);
    if (hit && hit.key === key) return hit;
    const c = proS.getContext('2d');
    c.setTransform(1, 0, 0, 1, 0, 0);
    const longest = line.words.reduce((a, b) => b.text.length > a.text.length ? b : a, line.words[0]);
    const anyRole = line.words.some(w => w.role);
    const measure = it => {
      c.font = it.fontOf(it.px); c.letterSpacing = '0px';
      const tr = it.track * it.px + (L.capTrack - 2);
      it.font = it.fontOf(it.px);
      it.cw = it.chars.map(ch => c.measureText(ch).width);
      it.xs = it.chars.map((_, i) => c.measureText(it.chars.slice(0, i).join('')).width + i * tr);
      it.width = c.measureText(it.text).width + (it.chars.length - 1) * tr;
      it.tr = tr;
    };
    const items = line.words.map(w => {
      const big = st.layout === 'duo' && (anyRole ? !!w.role : w === longest);
      const k = st.look(w, big);
      const text = k.kase === 'lower' ? w.text.toLowerCase() : k.kase === 'upper' ? w.text.toUpperCase() : w.text;
      const it = { w, big, text, chars: [...text], px: k.px, fontOf: k.font, track: k.track };
      measure(it);
      // a shuffled order for the animations that bring letters in at random, the same on every frame
      it.order = it.chars.map((_, i) => i).sort((a, b) => noise(w.t * 13 + a * 7.1) - noise(w.t * 13 + b * 7.1));
      it.order = it.order.map((_, i) => it.order.indexOf(i));
      return it;
    });
    let rows = [items];
    if (st.layout === 'duo') {
      rows = [items.filter(i => !i.big), items.filter(i => i.big)].filter(r => r.length);
    } else if (st.layout === 'block') {   // a justified block: every row is scaled to the same width
      rows = [];
      let cur = [];
      const flush = () => { if (cur.length) { rows.push(cur); cur = []; } };
      for (const it of items) {
        if (it.w.role) { flush(); rows.push([it]); continue; }
        cur.push(it);
        if (cur.length >= 3 || cur.map(x => x.text).join(' ').length >= 11) flush();
      }
      flush();
      if (rows.length === 1 && items.length >= 4) { const h = Math.ceil(items.length / 2); rows = [items.slice(0, h), items.slice(h)]; }
      const BW = SIZE * 6.4;
      for (const row of rows) {
        const wide = row.reduce((a, it) => a + it.width, 0) + (row.length - 1) * row[0].px * 0.26;
        const k = clamp(BW / wide, 0.5, 2.5);
        for (const it of row) { it.px *= k; measure(it); }
      }
    }
    const out = { key, rows: [], wide: 0 };
    for (const items of rows) {
      const px = Math.max(...items.map(i => i.px));
      // wide-tracked text needs wider word spaces, or the words run together
      const gap = px * (st.gap ?? (st.layout === 'block' ? 0.26 : 0.3)) + Math.max(0, items[0].tr) * 1.7;
      const wide = items.reduce((a, it) => a + it.width, 0) + (items.length - 1) * gap;
      let x = -wide / 2;
      for (const it of items) { it.x = x; x += it.width + gap; }
      out.rows.push({ items, px, wide });
      out.wide = Math.max(out.wide, wide);
    }
    if (out.rows.length === 1) {
      out.rows[0].y = out.rows[0].px * 0.35;
      out.h = out.rows[0].px * 1.3;
    } else {
      const lead = st.leading ?? 1.05;
      out.h = out.rows.reduce((a, r) => a + r.px * lead, 0);
      let top = -out.h / 2;
      for (const r of out.rows) { r.y = top + r.px * (lead - 0.16); top += r.px * lead; }
    }
    out.fit = Math.min(1, W * 0.9 / out.wide);
    proCache.set(line, out);
    return out;
  }

  function captionPro(t, st) {
    const line = E.captions.find(l => t >= l.start - 0.08 && t < l.end);
    if (!line) return;
    if (!proS) proSetup();
    const lay = proLayout(line, st);
    const exit = inOut((line.end - t) / 0.22);
    const s = proS.getContext('2d'), f = proF.getContext('2d'), h = proH.getContext('2d');
    const reach = lay.h / 2 * lay.fit + 170;
    const y0 = Math.max(0, Math.floor((CAP_Y - reach) / 16) * 16), y1 = Math.min(H, Math.ceil((CAP_Y + reach) / 16) * 16), bh = y1 - y0;
    const drift = st.drift ? st.drift * Math.sin(t * 1.1) : 0;
    const life = clamp((t - line.start) / Math.max(0.3, line.end - line.start));
    const grow = lay.fit * (0.985 + 0.03 * life);   // the caption creeps toward you while it is up
    for (const c of [s, f, h]) {
      c.setTransform(1, 0, 0, 1, 0, 0);
      c.globalCompositeOperation = 'source-over'; c.globalAlpha = 1;
      c.clearRect(0, y0, W, bh);
      c.save();
      c.beginPath(); c.rect(0, y0, W, bh); c.clip();   // nothing lands outside the band that gets cleared
      c.setTransform(grow, 0, 0, grow, W / 2, CAP_Y + drift);
      c.textBaseline = 'alphabetic'; c.textAlign = 'left'; c.letterSpacing = '0px';
    }
    for (const row of lay.rows) {
      for (const it of row.items) {
        const age = t - it.w.t;
        if (age < -0.06) continue;
        const n = it.chars.length, stag = Math.min(st.stagger, 0.3 / n);
        const color = it.w.role ? P[it.w.role] || P.white : st.base || P.white;
        s.font = f.font = h.font = it.font;
        let paint = color;
        if (st.fill) {
          paint = s.createLinearGradient(0, -it.px * 0.74, 0, it.px * 0.06);
          for (const [at, col] of st.fill) paint.addColorStop(at, col);
        }
        const flick = st.flicker && age < 0.4 && noise(Math.floor(t * 30) + it.x) > 0.3 ? 0.4 : 1;
        if (!MOT && st.reveal === 'rise') {   // letters come up from behind an invisible edge
          s.save(); s.beginPath(); s.rect(it.x - it.px, row.y - it.px * 0.98, it.width + it.px * 2, it.px * 1.22); s.clip();
        }
        const wp = outExpo(clamp((age + 0.06) / (st.dur * 1.6)));   // the word as a whole, for reveals that move it as one
        for (let ci = 0; ci < n; ci++) {
          const p = MOT ? 1 : clamp((age + 0.06 - ci * stag) / st.dur);
          if (p <= 0) continue;
          const e = outExpo(p), cw = it.cw[ci], ch = it.chars[ci];
          let dx = 0, dy = 0, sc = 1, sharp = 1, soft = 0, heavy = 0, rot = 0;
          const cx = it.x + it.xs[ci] + cw / 2;
          const pivot = MOT && MOT.unit === 'word' ? it.x + it.width / 2 : cx;   // a word turns about its own middle
          if (MOT) {
            const m = motionAt(MOT, it, ci, n, age);
            if (!m) continue;
            ({ dx, dy, rot, sharp, soft, heavy } = m);
          } else if (st.reveal === 'blur') {
            sharp = inOut((p - 0.3) / 0.7); soft = Math.sin(Math.PI * Math.min(1, p * 1.15)) * 0.95;
            dy = (1 - e) * it.px * (st.rise ?? 0.12); sc = 1 + 0.1 * (1 - e);
          } else if (st.reveal === 'rise') {
            dy = (1 - e) * it.px * 1.08;
          } else if (st.reveal === 'track') {
            dx = (ci - (n - 1) / 2) * it.px * 0.9 * (1 - wp);
            sharp = inOut((wp - 0.25) / 0.75); soft = Math.sin(Math.PI * wp) * 0.9;
          } else if (st.reveal === 'slam') {
            sc = 1 + 1.5 * (1 - e); sharp = clamp(p * 4); soft = (1 - e) * 0.8;
          } else if (st.reveal === 'trail') {
            dy = (1 - e) * it.px * 0.9; sharp = clamp(p * 3);
          }
          let spin = 0;   // a turn about the letter's own middle
          if (TFX.has('wiggle')) {
            dx += it.px * 0.05 * smooth(t * 2.3, ci * 17 + it.x); dy += it.px * 0.07 * smooth(t * 2.0, ci * 31 + it.x + 7);
            spin += 4 * smooth(t * 1.8, ci * 13 + it.x + 3);
          }
          if (TFX.has('waves')) dy += it.px * 0.16 * Math.sin(t * 4.2 - cx / (it.px * 0.9));
          if (TFX.has('arc')) {   // the line bends over a wide circle
            const radius = Math.max(lay.wide * 1.7, 1000), ang = cx / radius;
            dy += radius * (1 - Math.cos(ang)); spin += ang * 180 / Math.PI;
          }
          const put = (c, a, down) => {
            if (a <= 0.004) return;
            c.save();
            c.globalAlpha = a * flick;
            if (rot) { c.translate(pivot + dx, row.y + dy); c.rotate(rot * Math.PI / 180); c.translate(-pivot - dx, -row.y - dy); }
            c.translate(cx + dx, row.y + dy + down);
            if (spin) c.rotate(spin * Math.PI / 180);
            if (st.skew) c.transform(1, 0, -st.skew, 1, 0, 0);
            if (sc !== 1) { c.translate(0, -it.px * 0.35); c.scale(sc, sc); c.translate(0, it.px * 0.35); }
            if (st.stroke) { c.lineJoin = 'round'; c.lineWidth = it.px * st.stroke * 2; c.strokeStyle = '#090b10'; c.strokeText(ch, -cw / 2, 0); }
            c.fillStyle = paint;
            if (TFX.has('outline')) {   // hollow letters: a bright edge, the bloom does the rest
              c.lineJoin = 'round'; c.lineWidth = Math.max(2, it.px * 0.035); c.strokeStyle = paint;
              c.strokeText(ch, -cw / 2, 0);
              c.globalAlpha *= 0.12;
            }
            c.fillText(ch, -cw / 2, 0);
            c.restore();
          };
          put(s, sharp, 0);
          put(f, soft, 0);
          put(h, heavy, 0);
          if (!MOT && st.reveal === 'trail') for (let k = 1; k <= 4; k++) put(f, (1 - e) * 0.55 / k, k * it.px * 0.2 * (1 - e) + it.px * 0.1);
        }
        if (!MOT && st.reveal === 'rise') s.restore();
        if (st.rule && it.w.role) {   // a hairline draws itself under the words that matter
          const grow = outExpo(clamp((age - 0.12) / 0.6));
          s.globalAlpha = 0.9 * grow; s.fillStyle = paint;
          s.fillRect(it.x, row.y + it.px * 0.2, it.width * grow, Math.max(2, it.px * 0.035));
          s.globalAlpha = 1;
        }
      }
    }
    for (const c of [s, f, h]) c.restore();
    if (TFX.has('cells') || TFX.has('flux')) {   // a moving texture inside the letters
      s.save();
      s.setTransform(1, 0, 0, 1, 0, 0);
      s.globalCompositeOperation = 'source-atop';
      if (TFX.has('cells')) {
        cellTex = cellTex || buildCells();
        const pat = s.createPattern(cellTex, 'repeat');
        pat.setTransform(new DOMMatrix().translate(t * 34, t * 14).scale(0.7));
        s.globalAlpha = 0.62; s.fillStyle = pat;
        s.fillRect(0, y0, W, bh);
      }
      if (TFX.has('flux')) {   // bands of the caption colours flowing through
        const cols = [P.cool || '#7de3ff', P.gold || '#ffd166', P.positive || '#9fd8ff', P.white];
        const off = (t * 0.22) % 1, g = s.createLinearGradient(-W * off, y0, W * (2 - off), y0 + bh * 0.6);
        for (let i = 0; i <= 8; i++) g.addColorStop(i / 8, cols[i % cols.length]);
        s.globalAlpha = 0.78; s.fillStyle = g;
        s.fillRect(0, y0, W, bh);
      }
      s.restore();
    }
    if (st.shimmer || TFX.has('sweep')) {   // a band of light crosses the letters, and only the letters
      s.save();
      s.setTransform(1, 0, 0, 1, 0, 0);
      s.globalCompositeOperation = 'source-atop';
      const at = ((t * 0.42) % 1.5) * (W + 700) - 350;
      const g = s.createLinearGradient(at - 170, y0, at + 170, y0 + bh * 0.5);
      g.addColorStop(0, 'rgba(255,255,255,0)'); g.addColorStop(0.5, 'rgba(255,255,255,0.72)'); g.addColorStop(1, 'rgba(255,255,255,0)');
      s.fillStyle = g;
      s.fillRect(0, y0, W, bh);
      s.restore();
    }
    if (TFX.has('cloud')) {   // the line condenses out of cloud
      const k = clamp((t - line.start + 0.08) / 0.9);
      if (k < 1) {
        cloudMasks = cloudMasks || buildClouds();
        for (const c of [s, f, h]) {
          c.save(); c.setTransform(1, 0, 0, 1, 0, 0);
          c.imageSmoothingQuality = 'high';
          c.globalCompositeOperation = 'destination-in';
          c.drawImage(cloudMasks[Math.min(23, Math.floor(k * 24))], 0, y0, W, bh);
          c.restore();
        }
      }
    }
    if (TFX.has('turb') && proTmp) {   // turbulent displace: the letters shiver in thin rows, like heat haze
      const m = proTmp.getContext('2d'), amp = SIZE * 0.085;
      m.clearRect(0, y0, W, bh);
      m.drawImage(proS, 0, y0, W, bh, 0, y0, W, bh);
      s.save(); s.setTransform(1, 0, 0, 1, 0, 0);
      s.clearRect(0, y0, W, bh);
      for (let y = y0; y < y0 + bh; y += 3) {
        const d = amp * (smooth(y * 0.035 + t * 2.4, 3) + 0.5 * smooth(y * 0.11 - t * 3.1, 41));
        s.drawImage(proTmp, 0, y, W, 3, d, y, W, 3);
      }
      s.restore();
    }
    proCompose(y0, bh, st, exit);
  }

  function proCompose(y0, bh, st, exit) {
    // shrink the sharp layer step by step: each smaller copy is a wider, softer version of the text
    let from = proS, d0 = 1;
    for (let i = 0; i < proPyr.length; i++) {
      const d = 2 << i, c = proPyr[i].getContext('2d');
      c.clearRect(0, y0 / d - 1, W / d, bh / d + 2);
      c.drawImage(from, 0, y0 / d0, W / d0, bh / d0, 0, y0 / d, W / d, bh / d);
      from = proPyr[i]; d0 = d;
    }
    const tinted = (src, d, colour) => {   // the text's shape in one flat colour, at 1/d size
      const c = proTint.getContext('2d');
      c.globalCompositeOperation = 'source-over';
      c.clearRect(0, 0, proTint.width, proTint.height);
      c.drawImage(src, 0, y0 / d, W / d, bh / d, 0, 0, W / d, bh / d);
      c.globalCompositeOperation = 'source-in';
      c.fillStyle = colour;
      c.fillRect(0, 0, W / d, bh / d);
    };
    const up = (src, d, alpha, dx = 0, dy = 0, own = false) => {
      if (alpha <= 0.003) return;
      ctx.globalAlpha = Math.min(1, alpha);
      if (own) ctx.drawImage(src, 0, 0, W / d, bh / d, dx, y0 + dy, W, bh);
      else ctx.drawImage(src, 0, y0 / d, W / d, bh / d, dx, y0 + dy, W, bh);
    };
    const glow = st.glow * L.capGlow;
    ctx.save();
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = 'high';
    // a dark seat, so the text reads over bright footage
    const seat = (st.seat ?? 0.55) * exit;
    if (TFX.has('shadow')) {   // a soft shadow thrown down and to the right
      const far = SIZE * 0.2;
      tinted(proPyr[2], 8, '#000'); up(proTint, 8, 0.6 * exit, far, far, true);
      tinted(proPyr[1], 4, '#000'); up(proTint, 4, 0.5 * exit, far * 0.8, far * 0.8, true);
    }
    tinted(proPyr[2], 8, '#000'); up(proTint, 8, seat, 0, 6, true);
    tinted(proPyr[1], 4, '#000'); up(proTint, 4, seat * 0.9, 0, 3, true);
    ctx.globalCompositeOperation = 'lighter';
    up(proPyr[3], 16, 0.5 * glow * exit); up(proPyr[2], 8, 0.5 * glow * exit); up(proPyr[1], 4, 0.42 * glow * exit);
    if (st.chroma) {   // red and cyan fringes, like a lens that cannot quite hold the letters together
      tinted(proPyr[0], 2, '#ff2a4d'); up(proTint, 2, 0.55 * exit, -st.chroma, 0, true);
      tinted(proPyr[0], 2, '#19e6ff'); up(proTint, 2, 0.55 * exit, st.chroma, 0, true);
    }
    ctx.globalCompositeOperation = 'source-over';
    if (MOT && MOT.blur) {   // far out of focus: the same letters shrunk three times over
      const t2 = proTint.getContext('2d'), t4 = proSoft.getContext('2d'), t8 = proHeavy.getContext('2d');
      t2.globalCompositeOperation = 'source-over';
      t2.clearRect(0, 0, proTint.width, proTint.height); t2.drawImage(proH, 0, y0, W, bh, 0, 0, W / 2, bh / 2);
      t4.clearRect(0, 0, proSoft.width, proSoft.height); t4.drawImage(proTint, 0, 0, W / 2, bh / 2, 0, 0, W / 4, bh / 4);
      t8.clearRect(0, 0, proHeavy.width, proHeavy.height); t8.drawImage(proSoft, 0, 0, W / 4, bh / 4, 0, 0, W / 8, bh / 8);
      up(proHeavy, 8, exit, 0, 0, true);
    }
    // letters still arriving: out of focus
    const q = proSoft.getContext('2d');
    q.clearRect(0, 0, proSoft.width, proSoft.height);
    q.drawImage(proF, 0, y0, W, bh, 0, 0, W / 4, bh / 4);
    up(proSoft, 4, exit, 0, 0, true);
    up(proPyr[1], 4, Math.sin(Math.PI * exit) * 0.8);   // and the whole line slips out of focus as it leaves
    up(proS, 1, exit * exit);
    if (st.hot) { ctx.globalCompositeOperation = 'lighter'; up(proS, 1, st.hot * exit); }
    ctx.restore();
  }

  function caption(t) {
    if (PRO[L.capStyle]) return captionPro(t, PRO[L.capStyle]);
    const line = E.captions.find(l => t >= l.start - 0.08 && t < l.end);
    if (!line) return;
    const life = clamp((t - line.start) / Math.max(0.3, line.end - line.start));
    const exit = inOut((line.end - t) / 0.14);
    const grow = 0.975 + 0.045 * (0.3 * outCubic(life / 0.2) + 0.7 * life);
    ctx.save();
    ctx.translate(W / 2, CAP_Y);
    ctx.scale(grow, grow);
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    const looks = line.words.map((w, i) => wordLook(w, i === 0));
    const widths = looks.map(k => { ctx.font = k.font; ctx.letterSpacing = `${k.track}px`; return ctx.measureText(k.text).width; });
    const gaps = looks.map(k => Math.round(k.px * 0.3125 * (ST.gap ?? 1)));
    const total = widths.reduce((a, b) => a + b, 0) + gaps.slice(1).reduce((a, b) => a + b, 0);
    if (total > W * 0.9) ctx.scale(W * 0.9 / total, W * 0.9 / total);   // big captions shrink to fit, never clip
    const still = !!ST.still, clean = !ST.mode && !ST.plain;
    let x = -total / 2;
    line.words.forEach((w, i) => {
      const k = looks[i], width = widths[i], age = t - w.t, cx = x + width / 2;
      x += width + (gaps[i + 1] || 0);
      const waiting = age < -0.05;
      if (waiting && ST.mode !== 'karaoke') return;
      const arrive = waiting || still ? 1 : outExpo((age + 0.05) / 0.3);
      const fill = waiting ? 0 : inOut((age - 0.04) / 0.2);
      const color = w.role ? P[w.role] || P.white : (ST.mode === 'karaoke' ? P.gold || '#ffe27a' : ST.base || P.white);
      const alpha = arrive * exit;
      if (alpha <= 0.002) return;
      ctx.save();
      ctx.font = k.font;
      ctx.letterSpacing = `${k.track}px`;
      let ox = (1 - arrive) * ANIMS[ANIM][0], oy = (1 - arrive) * ANIMS[ANIM][1] - (1 - exit) * 8;
      if (!still && !waiting) {
        if (ANIM === 'wiggle') {
          const n = Math.floor(t * 14) + i * 31;
          ox += 2.6 * noise(n); oy += 2.6 * noise(n + 977);
        } else if (ANIM === 'wave') {
          oy += 8 * Math.sin(t * 5.2 - i * 0.85);
        }
      }
      ctx.translate(cx + ox, oy);
      if (ST.tilt) ctx.rotate(ST.tilt * noise(i * 17 + line.start * 3) * Math.PI / 180);
      if (!still && !waiting) {
        if (ANIM === 'wiggle') ctx.rotate(0.025 * noise(Math.floor(t * 14) + i * 53));
        if (ANIM === 'custom') { ctx.rotate((1 - arrive) * L.capRot * Math.PI / 180); const s = L.capFromScale + (1 - L.capFromScale) * arrive; ctx.scale(s, s); }
      }
      let pop = still || waiting ? 1 : 0.9 + 0.1 * arrive + (w.role ? 0.05 * bell(age - 0.22, 0.12) : 0);
      if (ANIM === 'pop' && !still && !waiting) pop *= 0.45 + 0.55 * outExpo((age + 0.05) / 0.22) + 0.12 * bell(age - 0.12, 0.07);
      if ((ST.mode === 'impact' || ST.mode === 'box' || ST.mode === 'karaoke') && !waiting) pop *= 1 + (ST.mode === 'karaoke' ? 0.07 : 0.13) * bell(age - 0.06, 0.09);   // these land with a thump
      ctx.scale(pop, pop);
      // ghost echo behind emphasis words
      if (w.role && clean) {
        const e = outCubic(age / 0.9);
        ctx.save();
        ctx.globalAlpha = alpha * 0.28 * (1 - e);
        ctx.font = `${SIZE * (1.35 + 0.55 * e)}px EEDisplay`;
        ctx.letterSpacing = '4px';
        ctx.lineWidth = 1.2;
        ctx.strokeStyle = color;
        ctx.strokeText(w.text, 0, 6 + 8 * e);
        ctx.restore();
      }
      // canvas blur is the single most expensive op here: only pay for it while a word is arriving
      const soft = (clean || ST.plain) ? (1 - arrive) * 9 + (1 - exit) * 4 : 0;
      if (soft > 0.15) ctx.filter = `blur(${soft.toFixed(2)}px)`;
      paintWord(k, w, i, t, age, alpha, fill, arrive, color, width, waiting);
      ctx.restore();
    });
    ctx.restore();
  }

  // ---------- title card on the hero shot ----------
  function titleCard(t) {
    const hero = E.shots[E.shots.length - 1];
    const start = hero.start / FPS + 0.55;
    if (t < start) return;
    const q = outExpo((t - start) / 0.9);
    const fade = 1 - inOut((t - (END - 0.7)) / 0.45);
    ctx.save();
    ctx.translate(W / 2, H * 0.5);
    ctx.globalAlpha = q * fade;
    if (q < 0.99) ctx.filter = `blur(${((1 - q) * 10).toFixed(2)}px)`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#ffffff';
    ctx.shadowColor = 'rgba(0,0,0,0.6)';
    ctx.shadowBlur = 20;
    ctx.font = '96px EEDisplay';
    ctx.letterSpacing = `${(26 - 12 * q).toFixed(1)}px`;
    ctx.fillText(E.title.toUpperCase(), 0, 0);
    const under = outExpo((t - start - 0.35) / 0.8);
    if (under > 0.01) {
      ctx.filter = 'none';
      ctx.globalAlpha = under * fade * 0.9;
      ctx.fillRect(-170 * under, 74, 340 * under, 2);
      if (E.year) {
        ctx.font = '600 26px EECaption';
        ctx.letterSpacing = `${(18 - 6 * under).toFixed(1)}px`;
        ctx.fillText(String(E.year), 0, 112);
      }
    }
    ctx.restore();
  }

  let lastTime = 0;
  function draw(time) {
    lastTime = time;
    const t = Math.max(0, time) + (window.SEGMENT_START || 0);
    const speaking = t < M + 0.05;
    // captions go through the shader when they should sit behind the subject
    capReady = !!(G && L.capBehind && depthVideo && speaking);
    if (capReady) {
      ctx = capCtx;
      ctx.clearRect(0, 0, W, H);
      caption(t);
      ctx = fxCtx;
    }
    film(t, camera(t));
    ctx.clearRect(0, 0, W, H);
    if (!G) {   // never fail quietly: an export without the shader pass should be obvious
      ctx.fillStyle = '#ff5570'; ctx.font = '40px sans-serif';
      ctx.fillText('WebGL is not available: the footage effects are off', 60, 80);
    }
    if (speaking) { if (!capReady) caption(t); }
    else if (t >= E.shots[E.shots.length - 1].start / FPS) titleCard(t);
    if (t < 0.25) { ctx.fillStyle = `rgba(0,0,0,${(1 - inOut(t / 0.25)).toFixed(3)})`; ctx.fillRect(0, 0, W, H); }
    if (t > END - 0.45) { ctx.fillStyle = `rgba(0,0,0,${inOut((t - END + 0.45) / 0.45).toFixed(3)})`; ctx.fillRect(0, 0, W, H); }
  }

  window.drawFrame = draw;
  window.addEventListener('hf-seek', e => draw(e.detail.time));
  // when the renderer swaps in the still for a frame, paint again from that exact picture
  // (footage and depth stills arrive together: one repaint covers both)
  let repaint = false;
  const paintSoon = () => { if (repaint) return; repaint = true; queueMicrotask(() => { repaint = false; draw(lastTime); }); };
  new MutationObserver(list => {
    for (const m of list) {
      const el = m.type === 'childList' ? [...m.addedNodes].find(n => n.classList && n.classList.contains('__render_frame__')) : m.target;
      if (!el || !el.classList || !el.classList.contains('__render_frame__')) continue;
      if (el.complete && el.naturalWidth) paintSoon();
      else el.addEventListener('load', paintSoon, { once: true });
    }
  }).observe(video.parentNode, { childList: true, subtree: true, attributes: true, attributeFilter: ['src'] });
  Promise.all([
    ...[...new Set(PRO[L.capStyle]
      ? [{}, { role: 'gold' }].flatMap(w => [false, true].map(big => { const k = PRO[L.capStyle].look(w, big); return k.font(k.px); }))
      : [ST.font({}), ST.font({ role: 'gold' })])].map(f => document.fonts.load(f)),
    document.fonts.load('96px EEDisplay'),
  ]).then(() => draw(0));
})();
