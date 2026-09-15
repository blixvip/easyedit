'use strict';
// easyedit film layer: camera moves on the footage + kinetic captions/grain on a canvas.
// Everything is a pure function of time so any frame can be rendered in isolation.
(() => {
  const E = window.EDIT;
  const W = E.width, H = E.height, FPS = E.fps;
  const video = document.getElementById('footage');
  const canvas = document.getElementById('fx');
  const ctx = canvas.getContext('2d');
  const blurNode = document.getElementById('dirblurNode');
  const P = Object.assign({ white: '#fbf8f4' }, E.palette);
  const M = E.montageStart / FPS;
  const END = E.frames / FPS;

  const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
  const outCubic = x => 1 - Math.pow(1 - clamp(x), 3);
  const outExpo = x => (x = clamp(x)) === 1 ? 1 : 1 - Math.pow(2, -10 * x);
  const inOut = x => (x = clamp(x), x * x * (3 - 2 * x));
  const bell = (x, w) => Math.exp(-(x * x) / (w * w));

  const GRADES = {
    neutral: 'contrast(1.06) saturate(1.08)',
    warm: 'contrast(1.07) saturate(1.1) sepia(0.12)',
    cool: 'contrast(1.08) saturate(1.02) hue-rotate(-6deg)',
    'teal-orange': 'contrast(1.1) saturate(1.18)',
    noir: 'contrast(1.12) saturate(0.55)',
  };
  const grade = GRADES[E.grade] || GRADES.neutral;

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

  function beatPulse(t) {
    let p = 0;
    for (const b of E.beats) {
      const age = t - b;
      if (age >= 0 && age < 0.4) p = Math.max(p, Math.exp(-age / 0.09));
    }
    return p;
  }

  // ---------- camera ----------
  function camera(t) {
    const cam = { zoom: 1.03, tx: 0, ty: 0, rot: 0, bx: 0, by: 0, stretch: 1, flash: 0, mono: 0,
                  ox: W / 2, oy: H / 2 };
    if (t < M) {
      cam.zoom += 0.035 * inOut(t / M);
      const inside = inOut((t - 0.05) / 0.55) * (1 - inOut((t - (M - 0.55)) / 0.5));
      const f = faceAt(t);
      if (f) {
        const tight = clamp(260 / Math.max(f.size, 60), 0.4, 1.4);
        cam.zoom += 0.13 * tight * inside;
        cam.tx = clamp((W / 2 - f.x) * 0.5, -130, 130) * inside;
        cam.ty = clamp((H * 0.42 - f.y) * 0.32, -45, 45) * inside;
      } else {
        cam.zoom += 0.08 * inside;
      }
      const kick = emphasisKick(t);
      cam.zoom += 0.022 * kick;
      cam.flash = 0.05 * kick;
      return cam;
    }
    const frame = t * FPS;
    const shot = E.shots.find(s => frame >= s.start && frame < s.end) || E.shots[E.shots.length - 1];
    const age = (frame - shot.start) / FPS;
    const dur = (shot.end - shot.start) / FPS;
    const left = dur - age;
    if (shot.face) { cam.ox = shot.face[0]; cam.oy = shot.face[1]; }
    const pulse = beatPulse(t);

    if (shot.hero) {
      cam.mono = inOut(age / 0.3);
      cam.zoom = 1.06 + 0.08 * inOut(age / dur) + 0.035 * pulse + 0.12 * (1 - outExpo(age / 0.5));
      cam.by = 10 * (1 - outCubic(age / 0.35));
      cam.bx = 2 * (1 - outCubic(age / 0.35));
      cam.flash = 0.75 * Math.exp(-age * 12);
      return cam;
    }

    const enter = 1 - outExpo(age / 0.28);
    const exit = Math.pow(1 - clamp(left / 0.14), 3);
    const kind = shot.index % 4;
    const dir = shot.index % 8 < 4 ? 1 : -1;
    cam.zoom = 1.035 + 0.05 * inOut(age / dur) + 0.028 * pulse + 0.09 * exit;
    if (kind === 0) {            // punch in from a hard zoom
      cam.zoom += 0.2 * enter;
      cam.bx = cam.by = 5 * enter + 2.5 * exit;
    } else if (kind === 1 || kind === 3) {  // vertical whip
      const d = kind === 1 ? dir : -dir;
      cam.ty = d * (110 * enter - 60 * exit);
      cam.stretch = 1 + 0.12 * enter + 0.1 * exit;
      cam.by = 22 * enter + 18 * exit;
      cam.bx = 1.5 * (enter + exit);
    } else {                     // slide + roll
      cam.tx = dir * (70 * enter - 30 * exit);
      cam.rot = dir * 1.1 * enter;
      cam.bx = 16 * enter + 12 * exit;
      cam.by = 1.5 * (enter + exit);
      cam.zoom += 0.06 * enter;
    }
    cam.flash = (kind === 0 ? 0.28 : 0.1) * Math.exp(-age * 16) + 0.06 * exit;
    return cam;
  }

  function applyCamera(cam) {
    video.style.transformOrigin = `${cam.ox.toFixed(1)}px ${cam.oy.toFixed(1)}px`;
    video.style.transform =
      `translate(${cam.tx.toFixed(2)}px, ${cam.ty.toFixed(2)}px) rotate(${cam.rot.toFixed(3)}deg) ` +
      `scale(${cam.zoom.toFixed(4)}, ${(cam.zoom * cam.stretch).toFixed(4)})`;
    const filters = [];
    const bx = Math.max(0, cam.bx), by = Math.max(0, cam.by);
    if (bx > 0.1 || by > 0.1) {
      blurNode.setAttribute('stdDeviation', `${bx.toFixed(2)} ${by.toFixed(2)}`);
      filters.push('url(#dirblur)');
    }
    filters.push(grade);
    if (cam.mono > 0) filters.push(`grayscale(${cam.mono.toFixed(3)}) contrast(${(1 + 0.15 * cam.mono).toFixed(3)})`);
    video.style.filter = filters.join(' ');
  }

  // ---------- captions ----------
  const CAP_Y = H * 0.61;
  const SIZE = 58, GAP = 19;

  function caption(t) {
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
    ctx.font = `800 ${SIZE}px EECaption`;
    ctx.letterSpacing = '2px';
    const widths = line.words.map(w => ctx.measureText(w.text).width);
    const total = widths.reduce((a, b) => a + b, 0) + GAP * (line.words.length - 1);
    let x = -total / 2;
    line.words.forEach((w, i) => {
      const width = widths[i], age = t - w.t, cx = x + width / 2;
      x += width + GAP;
      if (age < -0.05) return;
      const arrive = outExpo((age + 0.05) / 0.3);
      const fill = inOut((age - 0.04) / 0.2);
      const color = w.role ? P[w.role] || P.white : P.white;
      const alpha = arrive * exit;
      if (alpha <= 0.002) return;
      ctx.save();
      ctx.translate(cx, (1 - arrive) * 14 - (1 - exit) * 8);
      const pop = 0.9 + 0.1 * arrive + (w.role ? 0.05 * bell(age - 0.22, 0.12) : 0);
      ctx.scale(pop, pop);
      // ghost echo behind emphasis words
      if (w.role) {
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
      ctx.filter = `blur(${((1 - arrive) * 9 + (1 - exit) * 4).toFixed(2)}px)`;
      // readability shadow, then outline-first reveal, then fill with glow
      ctx.shadowColor = 'rgba(0,0,0,0.55)';
      ctx.shadowBlur = 14;
      ctx.globalAlpha = alpha * (0.25 + 0.75 * (1 - fill)) * 0.9;
      ctx.lineWidth = 1.1;
      ctx.strokeStyle = color;
      ctx.strokeText(w.text, 0, 0);
      ctx.globalAlpha = alpha * fill;
      ctx.shadowColor = color;
      ctx.shadowBlur = w.role ? 26 : 12;
      ctx.fillStyle = color;
      ctx.fillText(w.text, 0, 0);
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
    ctx.filter = `blur(${((1 - q) * 10).toFixed(2)}px)`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillStyle = '#ffffff';
    ctx.shadowColor = 'rgba(0,0,0,0.6)';
    ctx.shadowBlur = 20;
    ctx.font = '96px EEDisplay';
    ctx.letterSpacing = `${(26 - 12 * q).toFixed(1)}px`;
    ctx.fillText(E.title.toUpperCase(), 0, 0);
    ctx.restore();
  }

  // ---------- film grain (seeded per frame so split renders match) ----------
  const grainTiles = [];
  (function makeGrain() {
    let seed = 1337;
    const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
    for (let n = 0; n < 8; n++) {
      const c = document.createElement('canvas');
      c.width = 480; c.height = 270;
      const g = c.getContext('2d');
      const img = g.createImageData(480, 270);
      for (let i = 0; i < img.data.length; i += 4) {
        const v = 128 + (rnd() - 0.5) * 255;
        img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
        img.data[i + 3] = 255;
      }
      g.putImageData(img, 0, 0);
      grainTiles.push(c);
    }
  })();

  function draw(time) {
    const t = Math.max(0, time) + (window.SEGMENT_START || 0);
    const cam = camera(t);
    applyCamera(cam);
    ctx.clearRect(0, 0, W, H);

    const vig = ctx.createRadialGradient(W / 2, H * 0.47, H * 0.38, W / 2, H / 2, H * 1.1);
    vig.addColorStop(0, 'rgba(0,0,0,0)');
    vig.addColorStop(1, 'rgba(0,0,0,0.42)');
    ctx.fillStyle = vig;
    ctx.fillRect(0, 0, W, H);

    if (t < M + 0.05) caption(t);
    else if (t >= E.shots[E.shots.length - 1].start / FPS) titleCard(t);

    if (cam.flash > 0.002) {
      ctx.fillStyle = `rgba(255,252,246,${clamp(cam.flash).toFixed(3)})`;
      ctx.fillRect(0, 0, W, H);
    }

    ctx.save();
    ctx.globalCompositeOperation = 'overlay';
    ctx.globalAlpha = 0.07;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(grainTiles[Math.floor(t * FPS) % grainTiles.length], 0, 0, W, H);
    ctx.restore();

    if (t < 0.25) { ctx.fillStyle = `rgba(0,0,0,${(1 - inOut(t / 0.25)).toFixed(3)})`; ctx.fillRect(0, 0, W, H); }
    if (t > END - 0.45) { ctx.fillStyle = `rgba(0,0,0,${inOut((t - END + 0.45) / 0.45).toFixed(3)})`; ctx.fillRect(0, 0, W, H); }
  }

  window.drawFrame = draw;
  window.addEventListener('hf-seek', e => draw(e.detail.time));
  Promise.all([
    document.fonts.load(`800 ${SIZE}px EECaption`),
    document.fonts.load('96px EEDisplay'),
  ]).then(() => draw(0));
})();
