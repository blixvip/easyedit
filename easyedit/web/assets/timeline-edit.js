// The editor's sequence controls. Every committed edit goes through change(), so saving and undo
// use the same document as preview/export. Pointer drags only move the DOM until the pointer lifts.
(() => {
  'use strict';
  const el = id => document.getElementById(id);
  const safe = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const get = fn => { try { return fn(); } catch { return undefined; } };
  const sequence = () => get(() => doc.seq);
  const fps = () => sequence()?.fps || get(() => doc.settings.fps) || 60;
  const scale = () => get(() => pps) || 30;
  const frame = value => Math.round(Math.max(0, value) * fps()) / fps();
  const round = value => Math.round(value * 1000) / 1000;
  const bound = (value, low, high) => Math.min(high, Math.max(low, value));
  const number = (value, fallback = 0) => Number.isFinite(Number(value)) ? Number(value) : fallback;
  const uid = () => `c${Date.now().toString(36)}${(++serial).toString(36)}`;
  const name = clip => clip.src?.split(':').slice(1).join(':') || 'Clip';
  const trackName = (kind, index) => `${kind === 'video' ? 'V' : 'A'}${index + 1}`;
  const locked = () => !!get(() => running || starting);
  const notice = message => get(() => toast(message, true));
  let serial = 0, mounted = false, drag = null, lastWidth = 0, media = {}, lastVideoTrack = 0, lastAudioTrack = 0;
  let linkedEdits = true, swallowClick = false;
  const waves = new Map();

  function records(seq = sequence()) {
    const result = [];
    if (!seq) return result;
    for (const kind of ['video', 'audio']) (seq[kind] || []).forEach((track, ti) => {
      track.forEach((clip, index) => result.push({ clip, kind, track: ti, index }));
    });
    return result;
  }
  function find(id, seq = sequence()) { return records(seq).find(r => r.clip.id === id) || null; }
  function selectedRecord() { return get(() => pick.kind) === 'seq' ? find(pick.id) : null; }
  function selected() { return selectedRecord()?.clip || null; }
  function group(id, seq = sequence()) {
    const entry = find(id, seq);
    if (!entry) return [];
    if (!linkedEdits) return [entry];
    return records(seq).filter(r => r.clip.id === id || r.clip.id === entry.clip.link || r.clip.link === id);
  }
  function sort(seq) { for (const kind of ['video', 'audio']) for (const track of seq[kind] || []) track.sort((a, b) => a.at - b.at); }
  function edit(fn) {
    if (!sequence() || locked()) return false;
    change(d => { fn(d.seq); sort(d.seq); });
    return true;
  }
  function activate(id, inspect = true) {
    const rec = find(id);
    if (!rec) return;
    pick = { kind: 'seq', id };
    if (rec.kind === 'video') lastVideoTrack = rec.track; else lastAudioTrack = rec.track;
    get(() => player?.pause());
    if (inspect) get(() => showLeft('fx'));
    refreshSelection();
    drawInspector();
  }
  function refreshSelection() {
    const id = selected()?.id;
    for (const node of el('layers')?.querySelectorAll('[data-clip-id]') || []) {
      const on = node.dataset.clipId === id;
      node.classList.toggle('selected', on);
      node.setAttribute('aria-pressed', String(on));
    }
    document.body.classList.toggle('ee-seq-selected', !!id);
    for (const key of ['seqDuplicate', 'seqDelete', 'seqInspect']) if (el(key)) el(key).disabled = !id || locked();
  }

  const rampAt = (ramp, u) => ramp === 'in' ? 2.6 - 2.1 * u * u * (3 - 2 * u)
    : ramp === 'out' ? 0.5 + 2.1 * u * u * (3 - 2 * u)
      : ramp === 'hit' ? 0.45 + 1.95 * Math.pow(Math.abs(2 * u - 1), 1.5) : 1;
  // Frame midpoints match sequence.times_of, including a speed ramp which was split earlier.
  function sourceDelta(clip, duration) {
    if (!clip.ramp || !['in', 'out', 'hit'].includes(clip.ramp)) return duration * (clip.speed || 1);
    const f = fps(), count = Math.max(1, Math.round(clip.dur * f)), n = Math.round(Math.abs(duration) * f);
    const [a, b] = clip.rspan || [0, 1];
    let sum = 0;
    for (let i = 0; i < n; i++) {
      const t = duration < 0 ? -(i + 0.5) / count : (i + 0.5) / count;
      sum += rampAt(clip.ramp, bound(a + t * (b - a), 0, 1)) * (clip.speed || 1) / f;
    }
    return duration < 0 ? -sum : sum;
  }
  function sourceDuration(clip) { return number(media[clip.src]?.duration, Infinity); }
  function cropClip(original, newStart, newEnd) {
    const c = structuredClone(original), delta = newStart - original.at;
    c.at = round(newStart);
    c.dur = round(Math.max(1 / fps(), newEnd - newStart));
    c.in = round(Math.max(0, (original.in || 0) + sourceDelta(original, delta)));
    if (['in', 'out', 'hit'].includes(original.ramp)) {
      const [a, b] = original.rspan || [0, 1];
      c.rspan = [round(bound(a + delta / original.dur * (b - a), 0, 1)),
        round(bound(a + (newEnd - original.at) / original.dur * (b - a), 0, 1))];
      if (c.rspan[1] <= c.rspan[0]) delete c.rspan;
    }
    return c;
  }
  function shift(id, at, track, seq) {
    const target = find(id, seq);
    if (!target) return;
    const linked = group(id, seq), delta = Math.max(-Math.min(...linked.map(r => r.clip.at)), at - target.clip.at);
    for (const r of linked) r.clip.at = round(Math.max(0, r.clip.at + delta));
    if (track != null && track !== target.track && seq[target.kind][track]) {
      seq[target.kind][target.track].splice(target.index, 1);
      seq[target.kind][track].push(target.clip);
    }
  }
  function trim(id, start, end, seq) {
    const target = find(id, seq);
    if (!target) return;
    const left = start - target.clip.at, right = end - (target.clip.at + target.clip.dur);
    for (const rec of group(id, seq)) {
      const c = rec.clip;
      seq[rec.kind][rec.track][rec.index] = cropClip(c, c.at + left, c.at + c.dur + right);
    }
  }
  function split(at = get(() => now) || 0) {
    if (!sequence() || locked()) return;
    const t = frame(at), minimum = 1 / fps();
    let rec = selectedRecord();
    if (!rec || t <= rec.clip.at + minimum / 2 || t >= rec.clip.at + rec.clip.dur - minimum / 2) {
      rec = records().filter(r => r.kind === 'video' && !r.clip.off && !(sequence().off || []).includes(trackName(r.kind, r.track))
        && t > r.clip.at + minimum / 2 && t < r.clip.at + r.clip.dur - minimum / 2).sort((a, b) => b.track - a.track)[0];
    }
    if (!rec) return notice('Place the playhead inside a clip to split it.');
    const id = rec.clip.id, next = new Map();
    for (const r of group(id)) if (t > r.clip.at && t < r.clip.at + r.clip.dur) next.set(r.clip.id, uid());
    pick = { kind: 'seq', id: next.get(id) };
    edit(seq => {
      for (const r of group(id, seq)) {
        const c = r.clip, newId = next.get(c.id);
        if (!newId) continue;
        const left = cropClip(c, c.at, t), right = cropClip(c, t, c.at + c.dur);
        right.id = newId;
        if (right.link) { if (next.has(right.link)) right.link = next.get(right.link); else delete right.link; }
        seq[r.kind][r.track][r.index] = left;
        seq[r.kind][r.track].push(right);
      }
    });
  }
  function remove() {
    const c = selected();
    if (!c) return notice('Select a timeline clip to delete it.');
    const ids = new Set(group(c.id).map(r => r.clip.id));
    pick = { kind: null };
    edit(seq => {
      for (const kind of ['video', 'audio']) seq[kind] = seq[kind].map(track => track.filter(item => !ids.has(item.id)));
      for (const rec of records(seq)) if (ids.has(rec.clip.link)) delete rec.clip.link;
    });
  }
  function duplicate() {
    const c = selected();
    if (!c) return notice('Select a timeline clip to duplicate it.');
    const groupIds = group(c.id).map(r => r.clip.id), ids = new Map(groupIds.map(id => [id, uid()]));
    const delta = c.dur;
    pick = { kind: 'seq', id: ids.get(c.id) };
    edit(seq => {
      for (const id of groupIds) {
        const rec = find(id, seq), clip = structuredClone(rec.clip);
        clip.id = ids.get(id); clip.at = round(clip.at + delta);
        if (clip.link) { if (ids.has(clip.link)) clip.link = ids.get(clip.link); else delete clip.link; }
        seq[rec.kind][rec.track].push(clip);
      }
    });
  }
  function toggleTrack(kind, index) {
    const label = trackName(kind, index);
    edit(seq => { seq.off ||= []; seq.off = seq.off.includes(label) ? seq.off.filter(x => x !== label) : [...seq.off, label]; });
  }
  function addTrack(kind) {
    if (!['video', 'audio'].includes(kind)) return;
    if ((sequence()?.[kind]?.length || 0) >= 6) return notice('This edit supports up to six tracks of each type.');
    edit(seq => { seq[kind] ||= []; seq[kind].push([]); });
  }
  function updateClip(fn) {
    const id = selected()?.id;
    if (id) edit(seq => { const rec = find(id, seq); if (rec) fn(rec.clip); });
  }
  function updateOptions(fn) { updateClip(c => { c.opts ||= {}; fn(c.opts); }); }

  function addShot(id, options = {}) {
    const shot = get(() => S.shots.find(s => s.id === id));
    const filename = shot && get(() => S.sources.montage[shot.src]);
    if (!shot || !filename) return notice('That shot is no longer available.');
    const duration = Math.max(1 / fps(), shot.end - shot.start);
    if (options.replaceId) {
      const rec = find(options.replaceId);
      if (!rec || rec.kind !== 'video') return;
      pick = { kind: 'seq', id: rec.clip.id };
      return edit(seq => {
        const c = find(options.replaceId, seq).clip;
        c.src = `montage:${filename}`; c.shot = id; c.in = round(shot.start);
        if (c.kind !== 'hero') c.kind = 'shot';
        c.speed = round(bound(duration / c.dur, 0.1, 1));
        delete c.ramp; delete c.rspan;
      });
    }
    return addMedia(`montage:${filename}`, { ...options, kind: 'video', sourceIn: shot.start,
      duration: options.duration ?? Math.min(2, duration), shot: id });
  }
  function addMedia(src, options = {}) {
    if (!sequence() || typeof src !== 'string') return;
    const role = src.split(':')[0], kind = options.kind || (role === 'music' ? 'audio' : 'video');
    if (!['video', 'audio'].includes(kind)) return;
    const track = bound(number(options.track, kind === 'video' ? lastVideoTrack : lastAudioTrack), 0, Math.max(0, (sequence()[kind] || []).length - 1));
    const defaultDuration = Math.min(4, number(media[src]?.duration, 4));
    const c = { id: uid(), src, kind: role === 'music' ? 'music' : role === 'speech' ? 'speech' : kind === 'video' ? 'shot' : 'audio',
      at: round(frame(number(options.at, get(() => now) || 0))), dur: round(Math.max(1 / fps(), options.duration == null ? defaultDuration : number(options.duration, defaultDuration))),
      in: round(Math.max(0, number(options.sourceIn))) };
    if (options.shot) c.shot = options.shot;
    if (role === 'music') c.duck = true;
    const audio = kind === 'video' && options.linkAudio && media[src]?.hasAudio ? { ...structuredClone(c), id: uid(), kind: role === 'speech' ? 'speech' : 'audio', link: c.id } : null;
    if (audio) c.link = audio.id;
    pick = { kind: 'seq', id: c.id };
    edit(seq => {
      seq[kind] ||= [];
      while (seq[kind].length <= track) seq[kind].push([]);
      seq[kind][track].push(c);
      if (audio) {
        seq.audio ||= [];
        if (!seq.audio.length) seq.audio.push([]);
        seq.audio[Math.min(lastAudioTrack, seq.audio.length - 1)].push(audio);
      }
    });
    get(() => showLeft('fx'));
    return c.id;
  }

  function snap(value, clip, moving, alt) {
    if (alt || get(() => PREFS.snap) === false) return frame(value);
    const skip = new Set(group(clip.id).map(r => r.clip.id));
    const targets = [0, get(() => now) || 0, ...(get(() => S.timeline.beats) || [])];
    for (const rec of records()) if (!skip.has(rec.clip.id)) targets.push(rec.clip.at, rec.clip.at + rec.clip.dur);
    let answer = value, distance = 7 / scale();
    for (const target of targets) for (const offset of moving ? [0, clip.dur] : [0]) {
      const delta = Math.abs(value + offset - target);
      if (delta < distance) { distance = delta; answer = target - offset; }
    }
    return frame(answer);
  }
  function dragLimits(clip, edge, value) {
    const linked = group(clip.id), minimum = 1 / fps();
    if (edge === 'start') {
      const earliest = Math.max(0, ...linked.map(r => r.clip.at - (r.clip.in || 0) / Math.max(0.1, r.clip.speed || 1)));
      return bound(value, earliest, clip.at + clip.dur - minimum);
    }
    let latest = 3600;
    for (const rec of linked) {
      const c = rec.clip, duration = sourceDuration(c);
      if (Number.isFinite(duration) && !c.ramp) latest = Math.min(latest, c.at + (duration - (c.in || 0)) / (c.speed || 1));
    }
    return bound(value, clip.at + minimum, Math.max(clip.at + minimum, latest));
  }
  function startDrag(e) {
    if (!sequence() || locked()) return;
    const mode = get(() => tool) || 'sel', scroller = el('tlscroll');
    if (e.button === 1 || mode === 'hand') {
      if (![0, 1].includes(e.button)) return;
      e.preventDefault(); e.stopImmediatePropagation();
      const start = { x: e.clientX, y: e.clientY, left: scroller.scrollLeft, top: scroller.scrollTop };
      document.body.classList.add('ee-timeline-pan');
      const move = ev => { scroller.scrollLeft = start.left + start.x - ev.clientX; scroller.scrollTop = start.top + start.y - ev.clientY; };
      const done = () => {
        window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', done); window.removeEventListener('pointercancel', done);
        document.body.classList.remove('ee-timeline-pan'); swallowClick = true; setTimeout(() => { swallowClick = false; }, 0);
      };
      window.addEventListener('pointermove', move); window.addEventListener('pointerup', done); window.addEventListener('pointercancel', done);
      return;
    }
    if (mode === 'zoom' && e.button === 0) {
      e.preventDefault(); e.stopImmediatePropagation();
      const x = e.clientX - scroller.getBoundingClientRect().left, head = get(() => HEADW) || 292;
      const time = Math.max(0, (scroller.scrollLeft + x - head) / scale());
      get(() => zoomTimeline(e.altKey ? -12 : 12));
      scroller.scrollLeft = Math.max(0, head + time * scale() - x);
      swallowClick = true;
      window.addEventListener('pointerup', () => setTimeout(() => { swallowClick = false; }, 0), { once: true });
      return;
    }
    const button = e.target.closest('[data-clip-id]');
    if (!button || e.button !== 0 || locked()) return;
    const rec = find(button.dataset.clipId);
    if (!rec) return;
    e.preventDefault(); e.stopImmediatePropagation();
    activate(rec.clip.id);
    if (mode === 'cut') {
      const t = frame(rec.clip.at + (e.clientX - button.getBoundingClientRect().left) / scale());
      seek(t); split(t);
      swallowClick = true;
      window.addEventListener('pointerup', () => setTimeout(() => { swallowClick = false; }, 0), { once: true });
      return;
    }
    const sc = el('tlscroll'), edge = e.target.closest('[data-trim]')?.dataset.trim;
    drag = { id: rec.clip.id, original: structuredClone(rec.clip), kind: rec.kind, from: rec.track, track: rec.track,
      x: e.clientX, y: e.clientY, scroll: sc.scrollLeft, pps: scale(), edge, moved: false, at: rec.clip.at, dur: rec.clip.dur };
    button.classList.add('dragging');
    try { el('layers').setPointerCapture(e.pointerId); } catch { /* mouse fallback */ }
    const move = ev => {
      if (!drag) return;
      const delta = (ev.clientX - drag.x + sc.scrollLeft - drag.scroll) / drag.pps;
      if (Math.abs(ev.clientX - drag.x) + Math.abs(ev.clientY - drag.y) < 4 && !drag.moved) return;
      drag.moved = true;
      document.body.classList.add('ee-timeline-drag');
      const c = drag.original;
      if (edge) {
        const boundary = snap((edge === 'start' ? c.at : c.at + c.dur) + delta, c, false, ev.altKey);
        const next = dragLimits(c, edge, boundary);
        drag.at = edge === 'start' ? next : c.at;
        drag.dur = edge === 'start' ? c.at + c.dur - next : next - c.at;
      } else {
        drag.at = snap(c.at + delta, c, true, ev.altKey);
        const hit = document.elementFromPoint(ev.clientX, ev.clientY)?.closest('.ee-track');
        if (hit?.dataset.kind === drag.kind) drag.track = +hit.dataset.trackIndex;
      }
      const node = el('layers').querySelector(`[data-clip-id="${CSS.escape(drag.id)}"]`);
      if (node) {
        const target = el('layers').querySelector(`.ee-track[data-kind="${drag.kind}"][data-track-index="${drag.track}"] .ee-trackbody`);
        if (target && node.parentElement !== target) target.appendChild(node);
        node.style.left = `${drag.at * drag.pps}px`; node.style.width = `${Math.max(3, drag.dur * drag.pps - 1)}px`;
        node.classList.add('dragging');
      }
      const box = sc.getBoundingClientRect();
      if (ev.clientX > box.right - 24) sc.scrollLeft += 12;
      else if (ev.clientX < box.left + 24) sc.scrollLeft -= 12;
      if (ev.clientY > box.bottom - 20) sc.scrollTop += 8;
      else if (ev.clientY < box.top + 20) sc.scrollTop -= 8;
      if (el('sel')) el('sel').textContent = `${edge ? 'Trim' : 'Move'} · ${drag.at.toFixed(2)}s · ${drag.dur.toFixed(2)}s`;
    };
    const finish = ev => {
      const done = drag;
      drag = null;
      document.body.classList.remove('ee-timeline-drag');
      window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', cancel);
      if (!done) return;
      swallowClick = true; setTimeout(() => { swallowClick = false; }, 0);
      if (done.moved) edit(seq => edge ? trim(done.id, frame(done.at), frame(done.at + done.dur), seq) : shift(done.id, frame(done.at), done.track, seq));
      else { seek(frame(done.original.at + bound((ev.clientX - button.getBoundingClientRect().left) / done.pps, 0, done.original.dur - 1 / fps()))); draw(); }
    };
    const cancel = () => { drag = null; document.body.classList.remove('ee-timeline-drag'); window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', finish); window.removeEventListener('pointercancel', cancel); draw(); };
    window.addEventListener('pointermove', move); window.addEventListener('pointerup', finish); window.addEventListener('pointercancel', cancel);
  }

  function input(label, field, value, min, max, step = 0.01) {
    return `<label class="ee-field"><span>${label}</span><input type="number" data-seq-field="${field}" value="${safe(round(value))}" min="${min}" max="${max}" step="${step}" aria-label="${label}"></label>`;
  }
  function select(label, field, value, choices) {
    return `<label class="ee-field"><span>${label}</span><select data-seq-field="${field}" aria-label="${label}">${choices.map(([key, text]) => `<option value="${safe(key)}"${String(key) === String(value ?? '') ? ' selected' : ''}>${safe(text)}</option>`).join('')}</select></label>`;
  }
  function drawInspector() {
    const root = el('seqInspector'), rec = selectedRecord();
    if (!root) return;
    root.hidden = !rec;
    if (!rec) { root.dataset.clipId = ''; return; }
    if (root.contains(document.activeElement) && root.dataset.clipId === rec.clip.id) return;
    const c = rec.clip, opts = c.opts || {}, tf = opts.tf || {}, video = rec.kind === 'video';
    root.dataset.clipId = c.id;
    const options = (values, fallback) => [['', fallback], ...(values || []).filter(Boolean).map(value => [value, value])];
    root.innerHTML = `<div class="ee-inspector-head"><b>${safe(name(c))}</b><span>${trackName(rec.kind, rec.track)} · ${safe(c.kind)}${c.link ? ' · linked' : ''}</span></div>
      <div class="ee-fields">
      ${select('Track', 'track', rec.track, sequence()[rec.kind].map((_, i) => [i, trackName(rec.kind, i)]))}
      ${input('Timeline start (s)', 'at', c.at, 0, 3600)}${input('Duration (s)', 'dur', c.dur, round(1 / fps()), 3600)}
      ${input('Source in (s)', 'in', c.in || 0, 0, Number.isFinite(sourceDuration(c)) ? Math.max(0, sourceDuration(c) - 1 / fps()) : 36000)}
      ${input('Speed', 'speed', c.speed || 1, 0.1, 8, 0.1)}
      ${video ? select('Speed ramp', 'ramp', c.ramp, [['', 'Constant'], ['in', 'Fast to slow'], ['out', 'Slow to fast'], ['hit', 'Fast / slow / fast']]) : input('Volume', 'gain', c.gain ?? 1, 0, 4, 0.05)}
      </div>
      <div class="ee-checkrow"><label><input type="checkbox" data-seq-field="enabled"${c.off ? '' : ' checked'}>Clip enabled</label>
      ${c.kind === 'music' ? `<label><input type="checkbox" data-seq-field="duck"${c.duck === false ? '' : ' checked'}>Duck under speech</label>` : ''}
      ${c.link ? '<button type="button" data-seq-action="unlink">Unlink audio/video</button>' : ''}</div>
      ${video ? `<details open><summary>Transform</summary><div class="ee-fields">${input('Position X', 'tf.x', tf.x || 0, -1920, 1920, 1)}${input('Position Y', 'tf.y', tf.y || 0, -1080, 1080, 1)}${input('Scale (%)', 'tf.s', tf.s ?? 100, 10, 400, 1)}${input('Rotation (°)', 'tf.r', tf.r || 0, -180, 180, 1)}${input('Opacity (%)', 'tf.o', tf.o ?? 100, 0, 100, 1)}</div></details>
      <details open><summary>Movement and colour</summary><div class="ee-fields">${select('Move', 'move', opts.move, options(get(() => S.moves), 'None'))}${select('Grade', 'grade', opts.grade, options(get(() => S.grades), 'Project grade'))}${select('Easing', 'ease', opts.ease, options(get(() => S.eases), 'Default'))}</div></details>
      <details><summary>Effects</summary><div class="ee-effects">${(get(() => S.fx) || []).map(effect => `<label><input type="checkbox" data-seq-fx="${safe(effect)}"${(opts.fx || []).includes(effect) ? ' checked' : ''}>${safe(effect)}</label>`).join('')}</div></details>` : ''}
      <p class="ee-inspector-hint">Drag a clip to move it. Drag either edge to trim. Hold Alt to bypass snapping. Changes preview and save automatically.</p>`;
  }
  function fieldChange(e) {
    const control = e.target, field = control.dataset.seqField, effect = control.dataset.seqFx, rec = selectedRecord();
    if (!rec || (!field && !effect)) return;
    const id = rec.clip.id, clip = rec.clip;
    if (effect) return updateOptions(opts => { const set = new Set(opts.fx || []); control.checked ? set.add(effect) : set.delete(effect); opts.fx = [...set]; });
    if (field === 'enabled') return updateClip(c => { if (control.checked) delete c.off; else c.off = true; });
    if (field === 'duck') return updateClip(c => { c.duck = control.checked; });
    if (['move', 'grade', 'ease'].includes(field)) return updateOptions(opts => { if (control.value) opts[field] = control.value; else delete opts[field]; });
    if (field === 'ramp') return updateClip(c => { if (control.value) c.ramp = control.value; else delete c.ramp; delete c.rspan; });
    if (!Number.isFinite(control.valueAsNumber) && control.type === 'number') { control.value = round(number(clip[field])); return; }
    let value = bound(number(control.value), number(control.min, -Infinity), number(control.max, Infinity));
    if (field === 'in' && Number.isFinite(sourceDuration(clip))) value = Math.min(value, Math.max(0, sourceDuration(clip) - 1 / fps()));
    if (field === 'track') return edit(seq => shift(id, clip.at, +control.value, seq));
    if (field === 'at') return edit(seq => shift(id, frame(value), rec.track, seq));
    if (field === 'dur') return edit(seq => trim(id, clip.at, dragLimits(clip, 'end', frame(clip.at + value)), seq));
    if (field.startsWith('tf.')) return updateOptions(opts => { opts.tf ||= {}; opts.tf[field.slice(3)] = round(value); });
    edit(seq => {
      const linked = ['in', 'speed'].includes(field) ? group(id, seq) : [find(id, seq)];
      for (const item of linked) item.clip[field] = round(value);
    });
  }

  function clipMarkup(c, kind, track) {
    const sel = get(() => pick.kind === 'seq' && pick.id === c.id), role = c.src.split(':')[0];
    const label = `${c.kind === 'hero' ? 'Final shot' : c.kind === 'music' ? 'Music' : c.kind === 'speech' ? 'Speech' : 'Clip'} · ${name(c)}`;
    const filename = name(c), index = get(() => S.sources[role]?.indexOf(filename));
    const thumb = kind === 'video' && ['speech', 'montage'].includes(role) && index >= 0
      ? `${get(() => API)}/thumb?k=${role}&i=${index}&t=${Math.max(0, c.in || 0).toFixed(2)}` : '';
    return `<div role="button" tabindex="0" class="ee-clip blk ${kind}${c.kind === 'hero' ? ' hero' : ''}${sel ? ' selected' : ''}${c.off ? ' disabled-clip' : ''}${c.dur * scale() < 20 ? ' compact' : ''}"
      data-clip-id="${safe(c.id)}" data-kind="${kind}" data-track-index="${track}" aria-label="${safe(label)}" aria-pressed="${!!sel}"
      style="left:${c.at * scale()}px;width:${Math.max(3, c.dur * scale() - 1)}px" title="${safe(label)} · ${c.at.toFixed(2)}s–${(c.at + c.dur).toFixed(2)}s">
      <span class="ee-clip-label">${safe(label)}${c.link ? ' ⛓' : ''}${c.speed && c.speed !== 1 ? ` · ${Math.round(c.speed * 100)}%` : ''}</span>
      ${thumb ? `<span class="ee-clip-picture" style="background-image:url('${safe(thumb)}')"></span>` : kind === 'audio' ? '<canvas class="ee-clip-wave" aria-hidden="true"></canvas>' : ''}
      <span class="ee-trim start" data-trim="start" title="Trim clip start"></span><span class="ee-trim end" data-trim="end" title="Trim clip end"></span></div>`;
  }
  function paintWaves() {
    for (const canvas of el('layers').querySelectorAll('.ee-clip-wave')) {
      const rec = find(canvas.closest('[data-clip-id]').dataset.clipId);
      if (!rec) continue;
      const clip = structuredClone(rec.clip);
      if (!waves.has(clip.src)) waves.set(clip.src, fetch(`${get(() => API)}/wave?src=${encodeURIComponent(clip.src)}`)
        .then(r => r.ok ? r.json() : null).catch(() => null));
      waves.get(clip.src).then(wave => {
        if (!canvas.isConnected || !wave?.peaks?.length || !wave.rate) return;
        const width = Math.max(1, Math.min(2000, Math.round(clip.dur * scale()))), height = 18;
        canvas.width = width; canvas.height = height;
        const ctx = canvas.getContext('2d');
        ctx.clearRect(0, 0, width, height); ctx.fillStyle = '#c5efce';
        let from = clip.in || 0;
        const [u0, u1] = clip.rspan || [0, 1];
        for (let x = 0; x < width; x++) {
          const span = clip.dur / width * (clip.speed || 1) * rampAt(clip.ramp, u0 + (x + 0.5) / width * (u1 - u0));
          const a = Math.floor(from * wave.rate), b = Math.max(a + 1, Math.ceil((from + span) * wave.rate));
          let peak = 0;
          for (let i = Math.max(0, a); i < Math.min(wave.peaks.length, b); i++) peak = Math.max(peak, wave.peaks[i]);
          const h = Math.max(1, peak / 100 * height);
          ctx.fillRect(x, (height - h) / 2, 1, h); from += span;
        }
      });
    }
  }
  function drawTimeline(width) {
    mount();
    lastWidth = width || lastWidth;
    const seq = sequence();
    if (!seq || drag) return;
    document.body.classList.add('ee-sequence-editor');
    const rows = [];
    // Keep DOM rows in source order for stable track indices. CSS places the highest video track on top.
    for (const kind of ['video', 'audio']) (seq[kind] || []).forEach((track, index) => {
      const label = trackName(kind, index), off = (seq.off || []).includes(label);
      const order = kind === 'video' ? seq.video.length - index : seq.video.length + index + 1;
      rows.push(`<div class="ee-track ${kind}${off ? ' track-off' : ''}" data-kind="${kind}" data-track-index="${index}" style="order:${order}">
        <div class="ee-trackhead"><span class="ee-track-badge">${label}</span><span class="ee-track-name">${kind === 'video' ? 'Video' : 'Audio'} ${index + 1}</span>
        <button type="button" class="ee-track-toggle" data-track-toggle="1" data-kind="${kind}" data-track-index="${index}" aria-pressed="${off}" aria-label="${kind === 'video' ? 'Hide' : 'Mute'} ${label}" title="${kind === 'video' ? 'Hide' : 'Mute'} track">${kind === 'video' ? off ? 'Off' : 'On' : 'M'}</button></div>
        <div class="ee-trackbody lbars" style="width:${width}px">${track.map(c => clipMarkup(c, kind, index)).join('')}${!track.length ? '<span class="ee-empty-track">Drop a clip here</span>' : ''}</div></div>`);
    });
    el('layers').innerHTML = rows.join('');
    paintWaves();
    if (el('drop')) el('drop').hidden = true;
    for (const id of ['mvl', 'mvr', 'rm', 'del', 'lock', 'reset', 'stairBtn']) if (el(id)) el(id).hidden = true;
    if (el('splitBtn')) el('splitBtn').hidden = false;
    if (el('sel')) {
      const c = selected();
      el('sel').textContent = c ? `${name(c)} · ${c.dur.toFixed(2)}s` : 'Drag clips · trim edges · drop shots from Project';
      el('sel').title = 'All video and audio tracks are editable. Higher video tracks cover lower tracks.';
    }
    el('seqAddShot').disabled = get(() => pick.kind) !== 'shot';
    el('seqAddVideo').disabled = (seq.video || []).length >= 6;
    el('seqAddAudio').disabled = (seq.audio || []).length >= 6;
    refreshSelection(); drawInspector();
    get(() => moveHead(false)); get(() => drawMarks());
  }
  function mount() {
    if (mounted || !el('layers')) return;
    mounted = true;
    const bar = document.createElement('div');
    bar.id = 'seqToolbar';
    bar.setAttribute('aria-label', 'Timeline editing');
    bar.innerHTML = '<button id="seqDuplicate" title="Duplicate selected clip">Duplicate</button><button id="seqDelete" title="Delete selected clip (Delete)">Delete</button><button id="seqInspect">Clip controls</button><span class="ee-toolbar-divider"></span><button id="seqAddShot" title="Add the selected Project shot at the playhead">Add selected shot</button><button id="seqAddVideo">+ Video track</button><button id="seqAddAudio">+ Audio track</button><label class="ee-linked"><input id="seqLinked" type="checkbox" checked>Linked clips</label>';
    el('tl').insertBefore(bar, el('tlscroll'));
    bar.onclick = e => {
      const id = e.target.closest('button')?.id;
      if (id === 'seqDuplicate') duplicate();
      else if (id === 'seqDelete') remove();
      else if (id === 'seqInspect') { get(() => showLeft('fx')); get(() => showPanel('project')); }
      else if (id === 'seqAddShot' && get(() => pick.kind) === 'shot') addShot(pick.id);
      else if (id === 'seqAddVideo') addTrack('video');
      else if (id === 'seqAddAudio') addTrack('audio');
    };
    el('seqLinked').onchange = e => { linkedEdits = e.target.checked; };
    const tools = el('nleTools');
    if (tools) {
      const razor = tools.querySelector('[data-t="cut"]');
      if (razor) { razor.title = 'Razor tool: click a clip to split it'; razor.setAttribute('aria-label', razor.title); }
      tools.addEventListener('click', e => {
        const button = e.target.closest('[data-t="cut"]');
        if (!button || button.disabled) return;
        e.preventDefault(); e.stopImmediatePropagation();
        get(() => setTool('cut'));
      }, true);
      const syncTool = () => {
        const active = get(() => tool) || 'sel';
        for (const button of tools.querySelectorAll('[data-t]')) {
          if (['cut', 'sel', 'hand', 'zoom'].includes(button.dataset.t)) {
            button.classList.toggle('on', button.dataset.t === active);
            button.setAttribute('aria-pressed', String(button.dataset.t === active));
          }
        }
      };
      new MutationObserver(syncTool).observe(document.body, { attributes: true, attributeFilter: ['data-tool'] });
      syncTool();
    }
    const inspector = document.createElement('div');
    inspector.id = 'seqInspector'; inspector.className = 'ee-inspector scroll'; inspector.hidden = true;
    (el('lt-fx') || el('side')).prepend(inspector);
    inspector.addEventListener('change', e => {
      fieldChange(e);
      const c = selected(), field = e.target.dataset.seqField;
      // Keep clamped/frame-rounded values visible even while this control retains focus.
      if (c && e.target.type === 'number' && field) {
        const value = field.startsWith('tf.') ? c.opts?.tf?.[field.slice(3)] : c[field];
        if (value != null) e.target.value = round(value);
      }
    });
    inspector.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.matches('input')) { e.preventDefault(); e.target.blur(); } });
    inspector.onclick = e => {
      if (e.target.closest('[data-seq-action="unlink"]')) {
        const c = selected();
        if (c) edit(seq => { for (const r of group(c.id, seq)) delete r.clip.link; });
      }
    };
    const layers = el('layers');
    layers.addEventListener('pointerdown', startDrag, true);
    // The legacy editor delegates mouse handlers on #layers and #tlscroll. Own sequence events here.
    for (const event of ['mousedown', 'click', 'dblclick', 'contextmenu']) layers.addEventListener(event, e => {
      if (!sequence()) return;
      e.stopImmediatePropagation();
      if (event === 'contextmenu') { e.preventDefault(); return; }
      if (event === 'click') {
        if (swallowClick) return;
        const toggle = e.target.closest('[data-track-toggle]');
        if (toggle) return toggleTrack(toggle.dataset.kind, +toggle.dataset.trackIndex);
        const clip = e.target.closest('[data-clip-id]');
        if (clip) { activate(clip.dataset.clipId); return draw(); }
        const body = e.target.closest('.ee-trackbody');
        if (body) {
          const row = body.closest('.ee-track');
          if (row.dataset.kind === 'video') lastVideoTrack = +row.dataset.trackIndex; else lastAudioTrack = +row.dataset.trackIndex;
          pick = { kind: null }; seek(frame((e.clientX - body.getBoundingClientRect().left) / scale())); draw();
        }
      }
      if (event === 'dblclick') { get(() => showLeft('fx')); get(() => showPanel('project')); }
    }, true);
    layers.addEventListener('keydown', e => {
      const clip = e.target.closest('[data-clip-id]');
      if (clip && ['Enter', ' '].includes(e.key)) { e.preventDefault(); e.stopPropagation(); activate(clip.dataset.clipId); draw(); }
    }, true);
    layers.addEventListener('dragover', e => {
      const row = e.target.closest('.ee-track');
      if (!row || [...e.dataTransfer.types].includes('Files')) return;
      e.preventDefault(); e.stopImmediatePropagation();
      e.dataTransfer.dropEffect = 'copy'; row.classList.add('drop-target');
    }, true);
    layers.addEventListener('dragleave', e => {
      const row = e.target.closest('.ee-track');
      if (row && !row.contains(e.relatedTarget)) row.classList.remove('drop-target');
    }, true);
    layers.addEventListener('drop', e => {
      const row = e.target.closest('.ee-track');
      if (!row || [...e.dataTransfer.types].includes('Files')) return;
      e.preventDefault(); e.stopImmediatePropagation(); row.classList.remove('drop-target');
      let item;
      try { item = JSON.parse(e.dataTransfer.getData('text/plain')); } catch { return; }
      const body = row.querySelector('.ee-trackbody'), options = { kind: row.dataset.kind, track: +row.dataset.trackIndex,
        at: frame((e.clientX - body.getBoundingClientRect().left) / scale()) };
      if (item.id && options.kind === 'video') {
        const replace = e.target.closest('[data-clip-id]');
        if (replace && !e.shiftKey) options.replaceId = replace.dataset.clipId;
        addShot(item.id, options);
      } else if (item.src) addMedia(item.src, options);
    }, true);
  }

  window.EETimeline = { mount, draw: drawTimeline, selected, find, split, remove, duplicate, addShot, addMedia,
    toggleTrack, addTrack, updateClip, updateOptions, select: activate, setMedia: next => { media = next || {}; }, sourceDelta };
})();
