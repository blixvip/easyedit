// The editing-desk layer for the editor (see nle.css). It only dresses what editor.html draws: filmstrips and
// waveforms on the clips, V/A track headers, a tool strip, audio meters, and the transport under the viewer.
// It reads the page's own state (S, pps, now, player) and never writes to the edit.
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const get = f => { try { return f(); } catch { return undefined; } };
  const state = () => get(() => S) || null;
  const scale = () => get(() => pps) || 30;
  const icon = name => get(() => ic(name)) || '';
  const api = `/api/edit/${get(() => slug) || location.pathname.split('/').filter(Boolean).pop()}`;
  const safe = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  if (!el('tl') || !el('layers')) return;
  document.body.classList.add('nle');

  // ---------- stills, loaded two at a time so the page's own requests are never queued behind them ----------
  const stills = new Map();   // url -> 'ok' | 'bad' | [waiting elements]
  const queue = [];
  let busy = 0;
  function pump() {
    while (busy < 2 && queue.length) {
      const url = queue.shift(), img = new Image();
      busy++;
      const done = ok => {
        busy--;
        const waiting = stills.get(url);
        stills.set(url, ok ? 'ok' : 'bad');
        if (ok && Array.isArray(waiting)) for (const paint of waiting) paint();
        pump();
      };
      img.onload = () => done(true);
      img.onerror = () => done(false);
      img.src = url;
    }
  }
  function still(url, paint) {
    const have = stills.get(url);
    if (have === 'ok') return paint();
    if (have === 'bad') return;
    if (have) return void have.push(paint);
    stills.set(url, [paint]);
    queue.push(url);
    pump();
  }

  // ---------- loudness of one source, for the audio clips and the meters ----------
  const waves = new Map();   // "role:name" -> { rate, peaks } once read
  const asked = new Map();
  function waveOf(src) {
    if (!asked.has(src)) {
      asked.set(src, fetch(`${api}/wave?src=${encodeURIComponent(src)}`).then(r => r.json()).then(w => {
        if (w && w.peaks && w.peaks.length) waves.set(src, w);
        return waves.get(src) || null;
      }).catch(() => null));
    }
    return asked.get(src);
  }

  const KIND = {   // label colours: video in blues, sound in greens
    speech: ['#5f86d8', '#fff'], shot: ['#6d7fe3', '#fff'], hero: ['#d9a93a', '#111'],
    'a:speech': ['#3f9c92', '#fff'], 'a:music': ['#4fa86b', '#fff'], 'a:': ['#5d9f7c', '#fff'],
  };
  const clockOf = t => `${Math.floor(t / 60)}:${String(Math.floor(t % 60)).padStart(2, '0')}`;

  function drawWaveInto(canvas, peaks, rate, from, dur, colour) {
    const w = canvas.width, h = canvas.height, g = canvas.getContext('2d');
    g.clearRect(0, 0, w, h);
    g.fillStyle = colour;
    for (let x = 0; x < w; x++) {
      const a = Math.floor((from + x / w * dur) * rate), b = Math.max(a + 1, Math.floor((from + (x + 1) / w * dur) * rate));
      let m = 0;
      for (let i = a; i < b && i < peaks.length; i++) m = Math.max(m, peaks[i]);
      const bar = Math.max(1, m / 100 * (h - 4));
      g.fillRect(x, (h - bar) / 2, 1, bar);
    }
  }

  // ---------- the arranged timeline: V and A tracks ----------
  function dressSequence(st) {
    const seq = st.seq, rows = [...el('layers').children].filter(r => r.classList.contains('lrow'));
    const nV = (seq.video || []).length, p = scale();
    rows.forEach((row, k) => {
      const video = k < nV, i = video ? k : k - nV;
      const clips = ((video ? seq.video : seq.audio) || [])[i] || [];
      const track = `${video ? 'V' : 'A'}${i + 1}`, off = (seq.off || []).includes(track);
      row.classList.add(video ? 'nle-v' : 'nle-a');
      row.classList.toggle('nle-split', video && i === 0);
      row.classList.toggle('nle-empty', !clips.length);
      row.style.order = video ? nV - i : nV + 1 + i;   // V1 sits just above A1, as on an editing desk
      const head = row.querySelector('.lhead');
      if (head) {
        head.innerHTML = `<span class="nle-badge${i === 0 ? ' main' : ''}">${track}</span>
          <span class="nle-ticon">${icon(video ? 'video' : 'sound')}</span>
          <span class="nle-tname">${video ? 'Video' : 'Audio'} ${i + 1}</span>
          ${off ? '<span class="nle-toff" title="This track is switched off">Off</span>' : ''}
          <span class="nle-tinfo">${clips.length ? `${clips.length} clip${clips.length > 1 ? 's' : ''}` : 'empty'}</span>`;
      }
      const rowH = video ? 48 : 42, bodyH = rowH - 6 - 15;
      row.querySelectorAll('.seqclip').forEach((b, n) => {
        const c = clips[n];
        if (!c) return;
        const [role, ...rest] = String(c.src || '').split(':'), name = rest.join(':');
        const [bg, fg] = KIND[video ? (c.kind in KIND ? c.kind : 'shot') : `a:${c.kind in { speech: 1, music: 1 } ? c.kind : ''}`];
        b.classList.add('nle-clip');
        b.style.setProperty('--c', bg);
        b.style.setProperty('--k', fg);
        const width = Math.max(3, c.dur * p - 1);
        const title = c.kind === 'hero' ? 'Final shot' : c.kind === 'shot' ? 'Shot' : c.kind === 'speech' ? 'Speech' : c.kind === 'music' ? 'Song' : c.kind;
        const speed = c.speed && Math.abs(c.speed - 1) > 0.01 ? `<em>${Math.round(c.speed * 100)}%</em>` : '';
        b.innerHTML = `<span class="nle-lab"><b>${safe(width > 70 ? `${title} · ${name}` : title)}</b>${speed}</span>`;
        if (video) {
          const index = ((st.sources || {})[role] || []).indexOf(name);
          if (index < 0 || !['speech', 'montage'].includes(role)) return;
          const tile = Math.round(bodyH * 16 / 9), count = Math.max(1, Math.min(14, Math.ceil(width / tile)));
          const strip = document.createElement('span');
          strip.className = 'nle-strip';
          for (let j = 0; j < count; j++) {
            const cell = document.createElement('i');
            cell.style.width = `${tile}px`;
            const t = Math.max(0, Math.round((c.in + (j + 0.5) / count * c.dur * (c.speed || 1)) * 2) / 2);   // half-second steps: zooming reuses stills
            const url = `${api}/thumb?k=${role}&i=${index}&t=${t.toFixed(2)}&n=${encodeURIComponent(name)}`;
            still(url, () => { cell.style.backgroundImage = `url("${url}")`; });
            strip.appendChild(cell);
          }
          b.appendChild(strip);
        } else {
          const canvas = document.createElement('canvas');
          canvas.className = 'nle-wave';
          canvas.width = Math.max(1, Math.min(8000, Math.round(width)));
          canvas.height = bodyH;
          canvas.style.width = `${canvas.width}px`;
          b.appendChild(canvas);
          const paint = w => { if (w && canvas.isConnected) drawWaveInto(canvas, w.peaks, w.rate, c.in || 0, c.dur, 'rgba(214,245,226,.85)'); };
          if (waves.has(c.src)) paint(waves.get(c.src)); else waveOf(c.src).then(paint);
        }
      });
    });
    const cols = el('cols');
    if (cols) { const c4 = cols.querySelector('.c4'), last = cols.lastElementChild; if (c4) c4.textContent = 'Tracks'; if (last && last !== c4) last.textContent = ''; }
  }

  // ---------- the beat layout: shots get their own picture ----------
  function dressBeat(st) {
    const byId = new Map((st.shots || []).map(s => [s.id, s]));
    for (const c of el('layers').querySelectorAll('.clip[data-id]')) {
      const shot = byId.get(c.dataset.id), url = shot && get(() => shotThumb(shot));
      if (!url) continue;
      c.classList.add('nle-shot');
      still(url, () => { c.style.backgroundImage = `linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.3)), url("${url}")`; });
    }
    const speech = el('layers').querySelector('.lrow[data-speech] .clip');
    if (speech && st.quote) {
      const url = `${api}/thumb?k=speech&i=${st.quote.clip}&t=${(st.quote.start + 1).toFixed(2)}`;
      speech.classList.add('nle-shot');
      still(url, () => { speech.style.backgroundImage = `linear-gradient(180deg, rgba(0,0,0,.05), rgba(0,0,0,.3)), url("${url}")`; });
    }
    const cols = el('cols');
    if (cols) { const c4 = cols.querySelector('.c4'), last = cols.lastElementChild; if (c4) c4.textContent = 'Layer Name'; if (last && last !== c4) last.textContent = 'Duration'; }
  }

  function dressRuler() {
    for (const b of el('ruler').querySelectorAll('span b')) {
      const m = /^(\d+)s$/.exec(b.textContent);
      if (m) b.textContent = clockOf(+m[1]);
    }
  }

  let pending = false;
  function dress() {
    pending = false;
    const st = state();
    if (!st) return;
    try {
      if (st.seqSaved && st.seq) dressSequence(st); else dressBeat(st);
      dressRuler();
      syncTools();
      tools.style.top = meter.style.top = `${el('tlbar').offsetTop}px`;   // below the panel's tab row
    } catch (e) { console.warn('nle layer', e); }
  }
  const later = () => { if (!pending) { pending = true; requestAnimationFrame(dress); } };
  new MutationObserver(later).observe(el('layers'), { childList: true });
  new MutationObserver(later).observe(el('ruler'), { childList: true });

  // ---------- tool strip ----------
  const tools = document.createElement('div');
  tools.id = 'nleTools';
  tools.innerHTML = `<button data-t="sel" title="Selection tool (V)"></button>
    <button data-t="cut" title="Razor: cut the shot under the playhead in two (Ctrl+Shift+D)"></button>
    <button data-t="hand" title="Hand tool: drag the picture around (H)"></button>
    <button data-t="zoom" title="Zoom tool: click the picture to zoom in, Alt-click to zoom out (Z)"></button>
    <hr>
    <button data-t="in" title="Zoom the timeline in (=)"></button>
    <button data-t="out" title="Zoom the timeline out (-)"></button>
    <button data-t="fit" title="Fit the whole edit in the timeline"></button>`;
  el('tl').appendChild(tools);
  const SVG = d => `<svg class="i" viewBox="0 0 24 24">${d}</svg>`;
  const OWN = {
    cut: SVG('<path d="M5 3.5l9.5 9.5-6 6L3 13.500z" opacity=".25" fill="currentColor" stroke="none"/><path d="M14.5 3.500l6 6-12 11H5v-3.500zM11 7l6 6"/>'),
    in: SVG('<circle cx="10.5" cy="10.500" r="6.500"/><path d="M15.5 15.500L21 21M10.500 7.500v6M7.500 10.500h6"/>'),
    out: SVG('<circle cx="10.5" cy="10.500" r="6.500"/><path d="M15.5 15.500L21 21M7.500 10.500h6"/>'),
    fit: SVG('<path d="M3 6v12M21 6v12M7 12h10M7 12l3-3M7 12l3 3M17 12l-3-3M17 12l-3 3"/>'),
  };
  function syncTools() {
    const now = document.body.dataset.tool || 'sel';
    for (const b of tools.querySelectorAll('button')) {
      const t = b.dataset.t, twin = { sel: 'toolSel', hand: 'toolHand', zoom: 'toolZoom' }[t];
      if (!b.firstChild) b.innerHTML = OWN[t] || (twin && el(twin) ? el(twin).innerHTML : '');
      if (twin) b.classList.toggle('on', t === now);
      if (t === 'cut') b.disabled = !el('splitBtn') || el('splitBtn').hidden;
    }
  }
  tools.onclick = e => {
    const b = e.target.closest('button');
    if (!b || b.disabled) return;
    const t = b.dataset.t, zoom = el('zoom');
    if (t === 'cut') return el('splitBtn').click();
    if (['sel', 'hand', 'zoom'].includes(t)) { get(() => setTool(t)); return syncTools(); }
    if (!zoom) return;
    zoom.value = t === 'fit' ? zoom.min : Math.max(+zoom.min, Math.min(+zoom.max, +zoom.value + (t === 'in' ? 12 : -12)));
    zoom.dispatchEvent(new Event('input', { bubbles: true }));
  };
  new MutationObserver(syncTools).observe(document.body, { attributes: true, attributeFilter: ['data-tool'] });

  // ---------- audio meters: the loudness of the sound under the playhead while it plays ----------
  const meter = document.createElement('div');
  meter.id = 'nleMeter';
  const DB = [0, -6, -12, -18, -24, -36, -48];
  const place = db => Math.max(0, Math.min(1, 1 + db / 54));   // 0 dB at the top, -54 dB at the bottom
  meter.title = 'Audio meters';
  meter.innerHTML = `<div class="bars"><div class="scale">${DB.map(d => `<span style="top:${(1 - place(d)) * 100}%">${d}</span>`).join('')}</div>
    <div class="ch"><i></i><u></u></div><div class="ch"><i></i><u></u></div></div><div class="lr"><span>L</span><span>R</span></div>`;
  el('tl').appendChild(meter);
  const chans = [...meter.querySelectorAll('.ch')].map(c => ({ bar: c.querySelector('i'), hold: c.querySelector('u'), level: 0, peak: 0, held: 0 }));

  function loudness(t) {   // 0..1
    const st = state();
    if (!st) return 0;
    if (st.seqSaved && st.seq) {
      let sum = 0;
      (st.seq.audio || []).forEach((clips, i) => {
        if ((st.seq.off || []).includes(`A${i + 1}`)) return;
        for (const c of clips) {
          if (t < c.at || t >= c.at + c.dur) continue;
          const w = waves.get(c.src);
          if (!w) { waveOf(c.src); continue; }
          sum += (w.peaks[Math.floor(((c.in || 0) + t - c.at) * w.rate)] || 0) / 100;
        }
      });
      return Math.min(1, sum);
    }
    const w = get(() => wave);
    return w && w.peaks ? (w.peaks[Math.floor(t * w.rate)] || 0) / 100 : 0;
  }
  function tick(stamp) {
    requestAnimationFrame(tick);
    const playing = !!get(() => player && player.playing) && !get(() => muted);
    const t = get(() => now) || 0;
    chans.forEach((ch, k) => {
      const v = playing ? loudness(t + k * 0.01) * (get(() => +el('vol').value) ?? 1) : 0;
      const pos = v > 0.002 ? place(20 * Math.log10(v)) : 0;
      ch.level = pos > ch.level ? pos : Math.max(0, ch.level - 0.03);
      if (ch.level >= ch.peak) { ch.peak = ch.level; ch.held = stamp; } else if (stamp - ch.held > 900) ch.peak = Math.max(0, ch.peak - 0.012);
      if (ch.shown === ch.level && ch.shownPeak === ch.peak) return;
      ch.shown = ch.level; ch.shownPeak = ch.peak;
      ch.bar.style.transform = `scaleY(${ch.level.toFixed(3)})`;
      ch.hold.style.bottom = `calc(${(ch.peak * 100).toFixed(1)}% - 1px)`;
      ch.hold.style.opacity = ch.peak > 0.01 ? 1 : 0;
    });
  }
  requestAnimationFrame(tick);

  // ---------- transport under the viewer ----------
  const transport = el('transport'), mon = el('mon'), vfoot = el('vfoot');
  if (transport && mon && vfoot && vfoot.parentNode === mon) mon.insertBefore(transport, vfoot);

  later();
})();
