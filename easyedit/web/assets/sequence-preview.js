/* Source-backed program monitor. The editable sequence is the playback source of truth. */
(() => {
  'use strict';
  let options = {}, media = {}, mediaKey = '', mediaTask = null;
  let player = null, session = 0, lookKey = '', timer = null, readyTimer = null, pending = null;
  let rendered = null, loadedMode = 'source';
  let resumeTime = 0, resumePlaying = false;
  const clone = value => structuredClone(value);
  const r3 = value => +Number(value).toFixed(3);
  const getDoc = () => options.getDoc ? options.getDoc() : null;
  const getState = () => options.getState ? options.getState() : null;
  const slug = () => options.getSlug ? options.getSlug() : location.pathname.split('/').filter(Boolean).pop();
  const frame = () => typeof options.frame === 'function' ? options.frame() : options.frame || document.getElementById('frame');
  const fpsOf = d => Number(d?.settings?.fps || d?.seq?.fps || 60);
  const report = (state, message = '', retry = false) => options.onStatus?.({ state, message, retry });
  const styleKey = d => JSON.stringify([fpsOf(d), d?.plan?.palette, d?.plan?.grade, d?.plan?.look]);
  const signature = () => JSON.stringify(getDoc());
  const hasRendered = () => !!(rendered && getState()?.built?.exists
    && rendered.version === getState().built.version && rendered.signature === signature());
  const mode = () => hasRendered() ? 'rendered' : 'source';
  const previewKey = () => JSON.stringify([styleKey(getDoc()), mode(), hasRendered() && rendered.depth]);

  // Match sequence.flatten: highest enabled track wins, later clips win overlaps on one track.
  function flatten(d = getDoc()) {
    const seq = d?.seq || {}, fps = fpsOf(d), off = new Set(seq.off || []), spans = [];
    for (const [ti, track] of (seq.video || []).entries()) {
      if (off.has(`V${ti + 1}`)) continue;
      for (const c of track) {
        if (c.off) continue;
        const a = Math.round(c.at * fps), b = Math.max(a + 1, Math.round((c.at + c.dur) * fps));
        spans.push({ ti, clip: c, a, b });
      }
    }
    const audioEnds = sounds(d).map(c => {
      const start = Math.round(c.at * fps);
      return Math.max(start + 1, Math.round((c.at + c.dur) * fps));
    });
    const end = Math.max(0, ...spans.map(s => s.b), ...audioEnds);
    if (!end) return { pieces: [], end: 0, fps };
    const marks = [...new Set([0, end, ...spans.flatMap(s => [s.a, s.b])])].sort((a, b) => a - b), pieces = [];
    for (let i = 0; i + 1 < marks.length; i++) {
      const a = marks[i], b = marks[i + 1];
      let top = null;
      for (const span of spans) if (span.a <= a && span.b >= b && (!top || span.ti >= top.ti)) top = span;
      const clip = top?.clip || null, previous = pieces[pieces.length - 1];
      if (previous && previous.clip === clip) previous.end = b;
      else pieces.push({ clip, start: a, end: b });
    }
    return { pieces, end, fps };
  }

  function sounds(d = getDoc()) {
    const off = new Set(d?.seq?.off || []);
    return (d?.seq?.audio || []).flatMap((track, ti) => off.has(`A${ti + 1}`) ? [] : track.filter(c => !c.off));
  }

  // Match sequence.captions, including repeated dialogue and words on either side of a split.
  function captions(d = getDoc(), state = getState()) {
    if (!d) return [];
    const speech = `speech:${state?.sources?.speech?.[d.quote?.clip || 0]}`;
    const dialogue = sounds(d).filter(c => c.src === speech && (c.gain ?? 1) > 0), lines = [];
    for (const c of dialogue) {
      const speed = c.speed || 1, clipEnd = c.at + c.dur;
      for (const line of d.quote?.lines || []) {
        const words = line.filter(w => c.in - 1e-3 <= w.start && w.start < c.in + c.dur * speed).map(w => ({
          text: w.text, t: r3(c.at + (w.start - c.in) / speed), role: w.role ?? null,
          end: Math.min(clipEnd, c.at + (w.end - c.in) / speed),
        })).sort((a, b) => a.t - b.t);
        if (words.length) lines.push({ start: words[0].t, end: r3(words[words.length - 1].end), clipEnd, words });
      }
    }
    lines.sort((a, b) => a.start - b.start);
    for (let i = 0; i + 1 < lines.length; i++) {
      const a = lines[i];
      a.end = r3(Math.min(a.clipEnd, lines[i + 1].start, a.end + 1.2));
    }
    if (lines.length) {
      const last = lines[lines.length - 1];
      last.end = r3(Math.min(last.clipEnd, last.end + 0.5));
    }
    return lines.map(line => ({ start: line.start, end: line.end,
      words: line.words.map(({ text, t, role }) => ({ text, t, role })) }));
  }

  function beats(d, state, duration) {
    const song = sounds(d).find(c => c.kind === 'music' && (c.gain ?? 1) > 0);
    if (!song) return [];
    const speed = song.speed || 1;
    return (state?.music?.beats || []).filter(t => song.in <= t && t <= song.in + song.dur * speed)
      .map(t => r3(song.at + (t - song.in) / speed)).filter(t => t <= duration);
  }

  function faces(pieces, fps, state) {
    const points = [];
    for (const p of pieces) {
      const c = p.clip;
      if (!c || c.kind !== 'speech') continue;
      const speed = c.speed || 1;
      for (const [sourceTime, x, y, size] of state?.faces?.[c.src] || []) {
        const t = c.at + (sourceTime - c.in) / speed;
        if (t >= p.start / fps && t < p.end / fps) points.push([+t.toFixed(2), x, y, size]);
      }
    }
    return points.sort((a, b) => a[0] - b[0]);
  }

  function build() {
    const d = getDoc(), state = getState();
    if (!d?.seq) return null;
    if (hasRendered()) {
      const edit = clone(rendered.edit), duration = edit.frames / edit.fps;
      return { session, edit, mode: 'rendered', built: { depth: rendered.depth },
        media: { built: { key: 'built', name: 'Rendered preview', exists: true,
          video: rendered.video, audio: rendered.audio, hasAudio: true } },
        pieces: [{ start: 0, end: edit.frames, clip: { id: 'rendered', src: 'built', kind: 'shot', at: 0, dur: duration, in: 0 } }],
        sounds: [{ id: 'rendered-mix', src: 'built', kind: 'mix', at: 0, dur: duration, in: 0, gain: 1 }] };
    }
    const { pieces, end, fps } = flatten(d);
    let index = 0;
    const shots = pieces.map((p, i) => {
      const c = p.clip, entry = { index, start: p.start, end: p.end, id: c?.id || `gap${i}` };
      if (!c) return { ...entry, gap: true };
      if (c.kind === 'speech') entry.speech = true;
      else { if (c.kind === 'hero') entry.hero = true; index++; }
      for (const k of ['move', 'fx', 'ease', 'grade', 'tf']) if (c.opts?.[k] != null) entry[k] = clone(c.opts[k]);
      return entry;
    });
    const edit = { v: 2, fps, width: 1920, height: 1080, frames: Math.max(1, end),
      montageStart: shots.find(s => !s.speech && !s.gap)?.start ?? end,
      title: d.plan?.title || '', year: d.plan?.year ?? state?.plan?.year,
      palette: clone(d.plan?.palette || {}), grade: d.plan?.grade || 'neutral', look: clone(d.plan?.look || {}),
      captions: captions(d, state), faces: faces(pieces, fps, state), shots, beats: beats(d, state, end / fps) };
    return { session, edit, media, mode: 'source',
      pieces: pieces.map(p => ({ start: p.start, end: p.end, clip: p.clip ? clone(p.clip) : null })),
      sounds: sounds(d).filter(c => media[c.src]?.hasAudio !== false).map(c => ({
        id: c.id, src: c.src, kind: c.kind, at: c.at, dur: c.dur, in: c.in,
        speed: c.speed || 1, gain: c.gain ?? 1, duck: c.duck,
      })) };
  }

  async function refreshMedia(force = false) {
    const key = JSON.stringify([slug(), getState()?.sources]);
    if (!force && key === mediaKey) return media;
    if (mediaTask && !force) return mediaTask;
    const request = (async () => {
      const res = await fetch(`/api/edit/${encodeURIComponent(slug())}/media`);
      const data = await res.json();
      if (!res.ok || data.error) throw new Error(data.error || 'Source files could not be loaded.');
      media = data;
      mediaKey = key;
      return media;
    })();
    mediaTask = request;
    try { return await request; } finally { if (mediaTask === request) mediaTask = null; }
  }

  async function load() {
    clearTimeout(timer);
    const mine = ++session;
    if (player) { resumeTime = player.time; resumePlaying = player.playing; player.pause(); }
    player = null;
    options.onPlayer?.(null, null);
    report('loading', hasRendered() ? 'Loading rendered preview…' : 'Loading source preview…');
    clearTimeout(readyTimer);
    if (pending) { pending.resolve(null); pending = null; }
    try {
      if (!hasRendered()) await refreshMedia();
      if (mine !== session) return null;
      const payload = build(), target = frame();
      if (!payload || !target) throw new Error('The preview has no sequence to play.');
      loadedMode = mode();
      lookKey = previewKey();
      if (!payload.pieces.length) report('empty', 'Add a video clip to the timeline.');
      const result = new Promise(resolve => { pending = { resolve, session: mine }; });
      readyTimer = setTimeout(() => {
        if (mine !== session) return;
        report('error', 'Preview is taking longer to load. Try again.', true);
        if (pending?.session === mine) { pending.resolve(null); pending = null; }
      }, 15000);
      target.src = `/render/${encodeURIComponent(slug())}/live.html?v=${getState()?.built?.version || 0}-${mine}-${Date.now()}`;
      return result;
    } catch (error) {
      if (mine === session) report('error', error.message || 'Preview could not load.', true);
      return null;
    }
  }

  function push() {
    if (!getDoc()?.seq) return;
    const changedMedia = mediaKey !== JSON.stringify([slug(), getState()?.sources]);
    if (mode() !== loadedMode) {
      void load();
      return;
    }
    if (previewKey() !== lookKey || changedMedia && !hasRendered()) {
      clearTimeout(timer);
      timer = setTimeout(() => { load(); }, 100);
      return;
    }
    if (!player) return;
    const payload = build();
    player.setLive(payload);
    if (!payload.pieces.length) report('empty', 'Add a video clip to the timeline.');
  }

  async function useRendered() {
    const state = getState();
    if (!state?.built?.exists || !getDoc()?.seq) return false;
    const captured = signature(), version = state.built.version;
    const root = `/render/${encodeURIComponent(slug())}/`, stamp = `${version}-${Date.now()}`;
    try {
      const response = await fetch(`${root}edit.js?v=${stamp}`);
      if (!response.ok) throw new Error('The rendered edit metadata is unavailable.');
      const text = await response.text();
      const match = text.match(/^\s*window\.EDIT\s*=\s*([\s\S]*?)\s*;?\s*$/);
      if (!match) throw new Error('The rendered edit metadata is invalid.');
      const edit = JSON.parse(match[1]);
      if (!Number.isFinite(edit.frames) || edit.frames <= 0 || !Number.isFinite(edit.fps) || edit.fps <= 0) {
        throw new Error('The rendered edit has no playable frames.');
      }
      if (signature() !== captured || getState()?.built?.version !== version) return false;
      rendered = { signature: captured, version, edit,
        video: `${root}footage.mp4?v=${stamp}`, audio: `${root}soundtrack.m4a?v=${stamp}`,
        depth: state.built.depth ? `${root}depth.mp4?v=${stamp}` : null };
      return !!await load();
    } catch (error) {
      report('error', `Rendered preview could not load: ${error.message}`, true);
      return false;
    }
  }

  function configure(next) {
    options = { ...options, ...next };
    window.easyeditLive = build;
    window.easyeditReady = (api, loadedSession) => {
      if (loadedSession != null && loadedSession !== session) return;
      clearTimeout(readyTimer);
      player = api;
      const payload = build();
      api.seek(Math.max(0, Math.min(resumeTime, api.total - 0.001)));
      options.onPlayer?.(api, payload);
      if (resumePlaying) api.play();
      if (pending?.session === session) { pending.resolve(api); pending = null; }
    };
    window.easyeditTick = (t, playing) => options.onTick?.(t, playing);
    window.easyeditPreviewStatus = (status, loadedSession) => {
      if (loadedSession != null && loadedSession !== session) return;
      if (status.state === 'ready') clearTimeout(readyTimer);
      options.onStatus?.(status);
    };
    return window.EEPreview;
  }

  window.EEPreview = { configure, load, push, captions, flatten, build, refreshMedia, useRendered,
    total: () => { const f = flatten(); return f.end / f.fps; },
    get player() { return player; }, get media() { return media; }, get mode() { return mode(); } };
})();
