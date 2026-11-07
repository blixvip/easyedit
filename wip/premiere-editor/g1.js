//@@ globals
// the editing tools, drawn on the same 24 grid
Object.assign(PATHS, {
  track: 'M4 5v14|M8 8h8M8 12h12M8 16h8|M17 9l3 3-3 3',
  ripple: 'M5 4v16|M9 8h10M9 16h10|M16 5l3 3-3 3M16 13l3 3-3 3',
  roll: 'M12 3v18|M8 8l-4 4 4 4M16 8l4 4-4 4',
  razor: 't M7 6.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM7 12.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z|M7 6.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM7 12.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5zM9.2 10.4L20 5M9.2 13.6L20 19',
  slip: 'M3 7h18v10H3z|M9 12H5M15 12h4|M7 10l-2 2 2 2M17 10l2 2-2 2',
  markIn: 'M9 4v16|M9 4h6M9 20h6', markOut: 'M15 4v16|M15 4H9M15 20H9',
  insert: 'M12 3v8|M8.5 7.5L12 11l3.5-3.5|M4 15h16v5H4z|d M12 15v5',
  overwrite: 'M12 3v8|M8.5 7.5L12 11l3.5-3.5|t M4 15h16v5H4z|M4 15h16v5H4z',
  prevEdit: 'M6 5v14|f M18 6.1v11.8a.8.8 0 01-1.26.65l-8.3-5.9a.8.8 0 010-1.3l8.3-5.9a.8.8 0 011.26.65z',
  nextEdit: 'M18 5v14|f M6 6.1v11.8a.8.8 0 001.26.65l8.3-5.9a.8.8 0 000-1.3l-8.3-5.9A.8.8 0 006 6.1z',
  magnet: 'M6 4v8a6 6 0 0012 0V4|M10 4v8a2 2 0 004 0V4|M6 8h4M14 8h4',
  chain: 'M10 14a4 4 0 005.66 0l3-3a4 4 0 00-5.66-5.66l-1 1|M14 10a4 4 0 00-5.66 0l-3 3a4 4 0 005.66 5.66l1-1',
  marker: 'f M7 4h10v9l-5 5-5-5z',
  lock: 't M6 11h12v9H6z|M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 017 0v3', unlock: 'M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 016.8-1.2',
  music: 't M9 17a3 3 0 10-6 0 3 3 0 006 0zM21 15a3 3 0 10-6 0 3 3 0 006 0z|M9 17V5l12-2v12|M9 17a3 3 0 10-6 0 3 3 0 006 0zM21 15a3 3 0 10-6 0 3 3 0 006 0z',
});

let S = null;          // what the server says: the bin, the sequence, the build status
let doc = null;        // what you are editing: plan, quote, shot choices, settings, and the sequence itself
let past = [], future = [];
let player = null;     // the Program monitor's controls, once its page has loaded
let now = 0, pps = 30, filter = 'all';
let pick = { kind: null };   // {kind:'clip', id} | {kind:'word', l, w} | {kind:'shot', id}
let saving = false, dirty = false, running = false;
let L = null;          // the clip library: every source in the edit, and the sites to find more on
let site = 'youtube', hits = null, hitNote = '', finding = false;
let tool = 'sel', vz = 'fit', panX = 0, panY = 0, muted = false;
let binList = ls('bin') === 'list';
let MEDIA = {};        // every source as the Program monitor plays it
let sel = new Set();   // the selected clips, by id
let gapSel = null;     // a gap clicked on a track: Delete closes it
let linked = ls('linked') !== '0';
let targets = { video: 0, audio: 0 };   // the tracks Insert and Overwrite put clips on
let ws = 'edit';       // the workspace: editing, footage or songs
let waves = {};        // loudness peaks of each audio source, for the waveforms
let locked = new Set((() => { try { return JSON.parse(ls(`lk.${slug}`) || '[]'); } catch { return []; } })());

//@@ state
// ---------- state ----------
const emptySeq = fps => ({ v: 1, fps, video: [[], []], audio: [[], []], off: [] });
const pickDoc = s => {
  const d = {
    plan: structuredClone(s.plan), quote: { clip: s.quote.clip, lines: structuredClone(s.quote.lines) },
    curation: structuredClone(s.curation), settings: structuredClone(s.settings),
    seq: structuredClone(s.seq || emptySeq(s.settings.fps)),
  };
  while (d.seq.video.length < 3) d.seq.video.push([]);   // V1 to V3 and A1 to A3, like a new Premiere sequence
  while (d.seq.audio.length < 3) d.seq.audio.push([]);
  d.seq.off ||= [];
  return d;
};

async function load() {
  const res = await fetch(API).then(r => r.json()).catch(() => ({ error: 'The easyedit app did not answer.' }));
  if (res.error) {
    $('fatalText').textContent = res.error;
    $('fatal').style.display = 'grid';
    return false;
  }
  S = res;
  return true;
}

function change(fn) {   // every edit goes through here so it can be undone and saved
  past.push(JSON.stringify(doc));
  if (past.length > 200) past.shift();
  future = [];
  fn(doc);
  afterChange();
}

function afterChange() {
  dirty = true;
  draw();
  pushLive();
  save();
}

async function save() {
  if (saving) return;
  saving = true;
  while (dirty) {
    dirty = false;
    status();
    const res = await post('save', doc).catch(() => ({ error: 'The easyedit app did not answer.' }));
    if (res.error) { saving = false; status(res.error); toast(res.error, true); return; }
    S = res;
  }
  saving = false;
  status();
  if (player && builtMode !== wantBuilt()) pushLive();   // a render matched or stopped matching
}

function undo(from, to) {
  if (!from.length) return;
  to.push(JSON.stringify(doc));
  doc = JSON.parse(from.pop());
  sel = new Set([...sel].filter(id => findClip(id)));
  gapSel = null;
  if (pick.kind !== 'clip' || !sel.size) pick = { kind: null };
  afterChange();
}

function status(error) {
  const chip = $('chip');
  const empty = !doc.seq.video.some(t => t.length) && 'The sequence is empty: drag shots from the Project panel onto V1.';
  const bad = error || empty;
  chip.className = 'chip' + (bad ? ' err' : running || saving ? ' warn busy' : '');
  chip.textContent = bad || (running ? running : saving ? 'Saving…' : S.built.fresh ? 'Saved · Rendered' : 'Saved · Live preview');
  chip.title = bad || running || saving ? chip.textContent : S.built.fresh
    ? 'Your edit is saved and rendered: the Program monitor plays the exact cut, with the sound effects.'
    : 'Your edit is saved. The Program monitor plays it live from the footage; Render preview adds the sound effects and the depth map.';
  $('apply').disabled = $('export').disabled = !!running || !!empty;
  $('undo').disabled = !past.length;
  $('redo').disabled = !future.length;
  $('watch').hidden = !S.built.video;
  if (S.built.video) $('watch').href = S.built.video;
  $('watch').classList.toggle('old', !!S.built.exportOld);
  $('watch').textContent = S.built.exportOld ? 'Watch last export (before your latest changes)' : 'Watch last export';
  $('watch').title = S.built.exportOld ? 'This video was exported before your latest changes. Export again to bring it up to date.' : '';
}

//@@ preview
// ---------- the Program monitor plays the sequence live ----------
const RAMPF = { in: u => 2.6 - 2.1 * u * u * (3 - 2 * u), out: u => 0.5 + 2.1 * u * u * (3 - 2 * u),
                hit: u => 0.45 + 1.95 * Math.pow(Math.abs(2 * u - 1), 1.5) };
const FPS = () => (doc && doc.seq && doc.seq.fps) || (S && S.settings.fps) || 60;
const roundT = t => Math.round(t * FPS()) / FPS();   // to the frame
function frameSpan(c) { const f = FPS(), a = Math.round(c.at * f); return [a, Math.max(a + 1, Math.round((c.at + c.dur) * f))]; }
// the picture frame by frame: the top clip where clips stack, black in gaps (the same sums as sequence.flatten)
function flatten(seq = doc.seq) {
  const off = new Set(seq.off || []), spans = [];
  seq.video.forEach((t, ti) => { if (!off.has(`V${ti + 1}`)) for (const c of t) if (!c.off) spans.push([ti, c, ...frameSpan(c)]); });
  if (!spans.length) return { pieces: [], end: 0 };
  const end = Math.max(...spans.map(s => s[3]));
  const marks = [...new Set([0, end, ...spans.map(s => s[2]), ...spans.map(s => s[3])])].sort((a, b) => a - b);
  const pieces = [];
  for (let k = 0; k + 1 < marks.length; k++) {
    const a = marks[k], b = marks[k + 1];
    let top = null;
    for (const [ti, c, ca, cb] of spans) if (ca <= a && cb >= b && (!top || ti >= top[0])) top = [ti, c];
    const clip = top ? top[1] : null;
    if (pieces.length && pieces[pieces.length - 1].clip === clip) pieces[pieces.length - 1].end = b;
    else pieces.push({ clip, start: a, end: b });
  }
  return { pieces, end };
}
const speechKey = () => `speech:${S.sources.speech[doc.quote.clip]}`;
function dialogueClips(d = doc) {
  const off = new Set(d.seq.off || []), k = `speech:${S.sources.speech[d.quote.clip]}`;
  return d.seq.audio.flatMap((t, ti) => off.has(`A${ti + 1}`) ? [] : t.filter(c => c.src === k && !c.off));
}
// the caption lines where their words are heard (the same sums as sequence.captions)
function captionLines() {
  const dl = dialogueClips(), lines = [];
  doc.quote.lines.forEach((line, l) => {
    const words = [];
    line.forEach((w, i) => {
      const c = dl.find(x => x.in - 1e-3 <= w.start && w.start < x.in + x.dur * (x.speed || 1));
      if (!c) return;
      const sp = c.speed || 1;
      words.push({ text: w.text, t: +(c.at + (w.start - c.in) / sp).toFixed(3), end: c.at + (w.end - c.in) / sp, role: w.role, clipEnd: c.at + c.dur, w: i });
    });
    if (words.length) lines.push({ l, start: words[0].t, end: words[words.length - 1].end, clipEnd: words[words.length - 1].clipEnd, words });
  });
  lines.forEach((a, k) => { const b = lines[k + 1]; if (b) a.end = Math.min(b.start, a.end + 1.2); });
  if (lines.length) { const z = lines[lines.length - 1]; z.end = Math.min(z.clipEnd, z.end + 0.5); }
  return lines;
}
function seqBeats() {   // the song's beats, where they fall on the timeline now
  const song = doc.seq.audio.flat().find(c => c.kind === 'music' && !c.off);
  if (!song || !S.music.beats) return [];
  const sp = song.speed || 1;
  return S.music.beats.filter(b => b >= song.in && b <= song.in + song.dur * sp).map(b => +(song.at + (b - song.in) / sp).toFixed(3));
}
function filmOpts(o) {
  const out = {};
  if (!o) return out;
  if (o.move) out.move = o.move;
  if (o.fx && o.fx.length) out.fx = [...o.fx];
  for (const k of ['ease', 'grade', 'tf']) if (o[k]) out[k] = o[k];
  return out;
}
function liveFaces(pieces) {   // where the face is, from the last cut, wherever the speech sits now
  const out = [], f = FPS();
  for (const p of pieces) {
    const c = p.clip;
    if (!c || c.kind !== 'speech') continue;
    const list = (S.faces || {})[c.src] || [], sp = c.speed || 1, a = p.start / f, b = p.end / f;
    for (const s of list) { const t = c.at + (s[0] - c.in) / sp; if (t >= a && t < b) out.push([+t.toFixed(2), s[1], s[2], s[3]]); }
  }
  return out.sort((x, y) => x[0] - y[0]);
}
let builtMode = false;   // the Program monitor plays the rendered cut: exact, with its sound effects and depth
const wantBuilt = () => !!(S && S.built.exists && S.built.fresh && !S.built.missing && !dirty && !saving);
function buildLive() {
  const fps = FPS(), { pieces, end } = flatten();
  let idx = 0;
  const shots = pieces.map((p, k) => {
    const c = p.clip, e = { index: idx, start: p.start, end: p.end, id: c ? c.id : `gap${k}` };
    if (!c) { e.gap = true; return e; }
    if (c.kind === 'speech') e.speech = true;
    else { if (c.kind === 'hero') e.hero = true; idx++; }
    return Object.assign(e, filmOpts(c.opts));
  });
  const montage = (shots.find(s => !s.speech && !s.gap) || { start: end }).start;
  const edit = { v: 2, fps, width: 1920, height: 1080, frames: Math.max(1, end), montageStart: montage, title: doc.plan.title,
                 year: S.plan.year, palette: doc.plan.palette, grade: doc.plan.grade, look: doc.plan.look,
                 captions: captionLines().map(x => ({ start: x.start, end: x.end, words: x.words.map(w => ({ text: w.text, t: w.t, role: w.role })) })),
                 faces: liveFaces(pieces), shots, beats: seqBeats() };
  if (builtMode) {
    const v = S.built.version;
    return { edit, built: { depth: S.built.depth ? `depth.mp4?v=${v}` : null },
             media: { built: { video: `footage.mp4?v=${v}`, audio: `soundtrack.m4a?v=${v}` } },
             pieces: [{ start: 0, end: Math.max(1, end), clip: { src: 'built', at: 0, dur: end / fps, in: 0, speed: 1 } }],
             sounds: [{ id: 'mix', src: 'built', kind: 'mix', at: 0, in: 0, dur: end / fps }] };
  }
  const off = new Set(doc.seq.off || []);
  const sounds = doc.seq.audio.flatMap((t, ti) => off.has(`A${ti + 1}`) ? [] : t.filter(c => !c.off).map(c => ({
    id: c.id, src: c.src, kind: c.kind, at: c.at, dur: c.dur, in: c.in, speed: c.speed || 1, gain: c.gain ?? 1, duck: c.duck })));
  return { edit, media: MEDIA, sounds,
           pieces: pieces.map(p => ({ start: p.start, end: p.end, clip: p.clip ? { src: p.clip.src, at: p.clip.at, dur: p.clip.dur, in: p.clip.in,
                                                                                   speed: p.clip.speed || 1, ramp: p.clip.ramp, rspan: p.clip.rspan } : null })) };
}
window.easyeditLive = () => buildLive();
window.easyeditReady = api => {
  player = api;
  api.volume(muted ? 0 : +$('vol').value);
  api.seek(Math.min(now, Math.max(0, api.total - 0.01)));
};
let wasPlaying = null;
window.easyeditTick = (t, playing) => {
  now = t;
  if (playing !== wasPlaying) { wasPlaying = playing; $('play').innerHTML = ic(playing ? 'pause' : 'play'); }
  showTime(t);
  moveHead(playing && PREFS.follow);
  playGuard(t, playing);
};

const lookOf = () => JSON.stringify([doc.plan.palette, doc.plan.grade, doc.plan.look]);
let lookKey = '';
function pushLive() {   // the edit as it stands now, straight into the Program monitor; a new look reloads it
  if (!player) return;
  const key = lookOf();
  if ((lookKey && key !== lookKey) || builtMode !== wantBuilt()) { lookKey = key; return loadPreview(); }
  lookKey = key;
  player.setLive(buildLive());
}

function loadPreview() {
  player = null;
  wasPlaying = null;
  builtMode = wantBuilt();
  const gone = Object.values(MEDIA).filter(m => !m.exists).length;
  $('blank').style.display = gone && S.built.missing ? 'grid' : 'none';
  $('blankText').textContent = `${gone === 1 ? 'One of the files' : `${gone} of the files`} this edit is cut from ${gone === 1 ? 'is' : 'are'} no longer on this computer, so ${gone === 1 ? 'it' : 'they'} cannot play. Download the same files again to see and export the whole edit.`;
  lookKey = lookOf();
  $('frame').src = `/render/${slug}/live.html?v=${S.built.version}-${Date.now() % 1e6}`;
}

const ZS = [0.125, 0.25, 0.33, 0.5, 0.75, 1, 1.5, 2, 3, 4];
function fitK() {
  const box = $('stagewrap').getBoundingClientRect();
  return Math.max(0.02, Math.min((box.width - 28) / 1920, (box.height - 28) / 1080));
}
function fitStage() {
  const fit = fitK(), k = vz === 'fit' ? fit : vz;
  if (vz === 'fit') panX = panY = 0;
  const st = $('stage').style;
  st.width = `${1920 * k}px`; st.height = `${1080 * k}px`;
  st.marginLeft = `${-960 * k + panX}px`; st.marginTop = `${-540 * k + panY}px`;
  $('frame').style.transform = `scale(${k})`;
  $('mag').options[0].textContent = `Fit ${Math.round(fit * 100)}%`;
  $('mag').value = String(vz);
}
function zoomView(dir) {
  const k = vz === 'fit' ? fitK() : vz;
  const next = dir > 0 ? ZS.find(z => z > k * 1.01) : [...ZS].reverse().find(z => z < k * 0.99);
  if (next) { vz = next; fitStage(); }
}
function setTool(t) {
  tool = t;
  document.body.dataset.tool = t;
  for (const b of document.querySelectorAll('#tools [data-tool]')) b.classList.toggle('on', b.dataset.tool === t);
}
function showTime(t) {
  const fps = FPS();
  $('clock').textContent = $('vclock').textContent = tc(t);
  $('fnum').textContent = `${String(Math.round(t * fps)).padStart(5, '0')} (${fps.toFixed(2)} fps)`;
}
function seek(t) {
  now = Math.max(0, Math.min(t, Math.max(0, total() - 0.5 / FPS())));
  if (player) player.seek(now);
  moveHead(false);
  showTime(now);
}

//@@ source
// ---------- the Source monitor: watch a clip, mark its in and out, put it on the timeline ----------
let SRC = null;   // what the Source monitor holds: {key, role, a, b (in and out, source seconds), shot, title}
function openSource(key, a, b, extra = {}) {
  const m = MEDIA[key];
  if (!m || !m.exists) return toast('That clip is not on this computer.', true);
  const v = $('srcvid');
  const url = m.role === 'montage' ? m.video : m.audio;   // speech and songs are watched with their sound
  if (!v.src.endsWith(url)) v.src = url;
  const d = m.duration || Math.max(b || 0, 5);
  SRC = { key, role: m.role, a: clamp(a ?? 0, 0, d), b: clamp(b ?? d, 0.05, d), ...extra };
  if (SRC.b - SRC.a < 0.05) SRC.b = Math.min(d, SRC.a + 1);
  v.muted = m.role === 'montage' || muted;
  v.pause();
  v.currentTime = SRC.a;
  $('lt-source').classList.add('has');
  $('srcName').textContent = extra.title || m.name;
  if (ws === 'edit') showLeft('source');
  drawSource();
}
function drawSource() {
  if (!SRC) return;
  const m = MEDIA[SRC.key] || {}, d = m.duration || Math.max(SRC.b, 1), v = $('srcvid');
  $('srcRange').style.left = `${SRC.a / d * 100}%`;
  $('srcRange').style.width = `${Math.max(0.3, (SRC.b - SRC.a) / d * 100)}%`;
  $('srcHead').style.left = `${(v.currentTime || 0) / d * 100}%`;
  $('srcTc').textContent = tc(v.currentTime || 0);
  $('srcDur').textContent = `In ${clock(SRC.a)} · Out ${clock(SRC.b)} · ${tc(SRC.b - SRC.a)}`;
  $('srcPlay').innerHTML = ic(v.paused ? 'play' : 'pause');
}
function srcLoop() { if (SRC && !$('srcvid').paused) { if ($('srcvid').currentTime >= SRC.b) $('srcvid').pause(); drawSource(); requestAnimationFrame(srcLoop); } else drawSource(); }
$('srcvid').addEventListener('play', () => requestAnimationFrame(srcLoop));
$('srcvid').addEventListener('seeked', drawSource);
$('srcvid').addEventListener('pause', drawSource);
function srcMark(which) {
  if (!SRC) return;
  const t = $('srcvid').currentTime;
  if (which === 'in') { SRC.a = Math.min(t, SRC.b - 1 / FPS()); } else { SRC.b = Math.max(t, SRC.a + 1 / FPS()); }
  drawSource();
}
function srcToggle() {
  const v = $('srcvid');
  if (!SRC) return;
  if (v.paused) { if (player) player.pause(); if (v.currentTime >= SRC.b - 0.05 || v.currentTime < SRC.a) v.currentTime = SRC.a; v.play().catch(() => {}); }
  else v.pause();
}
$('srcScrub').addEventListener('pointerdown', e => {
  if (!SRC) return;
  const bar = $('srcScrub'), d = (MEDIA[SRC.key] || {}).duration || 1;
  const go = ev => { const r = bar.getBoundingClientRect(); $('srcvid').currentTime = clamp((ev.clientX - r.left) / r.width, 0, 1) * d; drawSource(); };
  $('srcvid').pause();
  go(e);
  bar.setPointerCapture(e.pointerId);
  const mv = ev => go(ev), up = () => { bar.removeEventListener('pointermove', mv); bar.removeEventListener('pointerup', up); };
  bar.addEventListener('pointermove', mv);
  bar.addEventListener('pointerup', up);
});
$('srcMarkIn').onclick = () => srcMark('in');
$('srcMarkOut').onclick = () => srcMark('out');
$('srcPlay').onclick = srcToggle;
$('srcInsert').onclick = () => placeSource('insert');
$('srcOverwrite').onclick = () => placeSource('overwrite');
$('srcvid').onclick = srcToggle;
// the marked part of the source, as the clips it makes at `at`: speech brings its sound, a song goes on a music track
function sourceClips(src, at, vti = targets.video, ati = null) {
  const dur = +Math.max(1 / FPS(), src.b - src.a).toFixed(3), id = uid(), inn = +src.a.toFixed(3);
  if (src.role === 'music') return [{ c: { id, src: src.key, kind: 'music', at, dur, in: inn, duck: true }, kind: 'audio', ti: ati ?? Math.max(1, targets.audio) }];
  const v = { id, src: src.key, kind: src.role === 'speech' ? 'speech' : 'shot', at, dur, in: inn, ...(src.shot ? { shot: src.shot } : {}) };
  const out = [{ c: v, kind: 'video', ti: vti }];
  if (src.role === 'speech') {
    const a = { id: uid(), src: src.key, kind: 'speech', at, dur, in: inn, link: v.id };
    v.link = a.id;
    out.push({ c: a, kind: 'audio', ti: ati ?? targets.audio });
  }
  return out;
}
function commitPlace(items, mode) {
  for (const { kind, ti } of items) if (locked.has(trackName(kind, ti))) return toast(`${trackName(kind, ti)} is locked.`, true);
  change(d => {
    for (const { c, kind, ti } of items) {
      while (d.seq[kind].length <= ti) d.seq[kind].push([]);
      const track = d.seq[kind][ti];
      if (mode === 'insert') rippleInsert(d, [[kind, ti]], c.at, c.dur); else clearRange(track, c.at, c.at + c.dur);
      track.push(structuredClone(c));
      track.sort((x, y) => x.at - y.at);
    }
  });
  sel = new Set(items.map(x => x.c.id));
  pick = { kind: 'clip', id: items[0].c.id };
  draw();
}
function placeSource(mode, at = now) {   // Insert (,) and Overwrite (.) at the playhead, on the targeted track
  if (!SRC) return toast('Click a shot in the Project panel to open it in the Source monitor first.', true);
  const items = sourceClips(SRC, roundT(at));
  commitPlace(items, mode);
  seek(at + items[0].c.dur);
  toast(mode === 'insert' ? 'Inserted at the playhead.' : 'Put in at the playhead.');
}

//@@ shots
// ---------- the Project panel's shots ----------
const uniq = a => [...new Set(a)];
const without = (a, id) => a.filter(x => x !== id);
const shotSrcKey = shot => `montage:${S.sources.montage[shot.src]}`;
function openShot(id) {
  const s = S.shots.find(x => x.id === id);
  if (!s) return;
  pick = { kind: 'shot', id };
  openSource(shotSrcKey(s), s.start, s.end, { shot: s.id, title: shotName(s) });
  drawBin();
}
function insertShot(id) { openShot(id); placeSource('insert'); }
function finalShot(id) {   // the final shot plays this shot from now on
  const s = S.shots.find(x => x.id === id);
  if (!s) return;
  const hero = doc.seq.video.flat().find(c => c.kind === 'hero');
  change(d => {
    d.curation.hero = id;
    d.curation.exclude = without(d.curation.exclude, id);
    const h = hero && findClip(hero.id, d);
    if (h) {
      h.c.src = shotSrcKey(s); h.c.shot = id;
      h.c.in = +Math.max(s.start, Math.min(s.end - h.c.dur, s.start + (s.end - s.start) * 0.25)).toFixed(3);
      delete h.c.rspan;
    }
  });
  toast(hero ? 'The final shot plays this shot now.' : 'It will be the final shot of the next beat cut.');
}
function hideShot(id) { change(d => { d.curation.exclude = uniq([...d.curation.exclude, id]); d.curation.pin = without(d.curation.pin, id); }); }
function unhideShot(id) { change(d => { d.curation.exclude = without(d.curation.exclude, id); }); }
