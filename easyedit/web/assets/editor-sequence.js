// Connect the timeline, existing caption/effect controls, live monitor, and saved document.
(() => {
  const find = (id, d = doc) => d?.seq?.video.flat().concat(d.seq.audio.flat()).find(c => c.id === id);
  const options = id => {
    const c = find(id);
    return c ? { ...(c.opts || {}), speed: c.speed || 1, ramp: c.ramp || 'none' } : doc.curation.shots[id] || {};
  };
  function editOptions(ids, fn) {
    change(d => {
      for (const id of ids) {
        const c = find(id, d);
        if (!c) continue;
        const o = { ...(c.opts || {}), speed: c.speed || 1, ramp: c.ramp || 'none' };
        fn(o);
        c.speed = Math.max(0.1, Math.min(8, Number(o.speed) || 1));
        c.ramp = ['in', 'out', 'hit'].includes(o.ramp) ? o.ramp : null;
        if (o.slip != null) {
          const s = S.shots.find(s => s.id === c.shot);
          if (s) c.in = s.start + Math.max(0, s.end - s.start - c.dur * c.speed) * Number(o.slip);
        }
        delete o.speed; delete o.ramp; delete o.slip;
        for (const key of Object.keys(o)) if (o[key] == null || Array.isArray(o[key]) && !o[key].length) delete o[key];
        c.opts = o;
      }
    });
  }
  function syncChoices(before, next) {
    if (!next.seq) return;
    next.seq.fps = next.settings.fps;
    if (before.settings.hero_length !== next.settings.hero_length) {
      for (const c of next.seq.video.flat()) if (c.kind === 'hero') c.dur = next.settings.hero_length;
    }
    // Older effect controls write curation by id; route those changes onto each matching clip.
    const old = before.curation.shots || {}, changed = next.curation.shots || {};
    for (const id of new Set([...Object.keys(old), ...Object.keys(changed)])) {
      if (JSON.stringify(old[id]) === JSON.stringify(changed[id])) continue;
      const o = changed[id] || {};
      for (const c of next.seq.video.flat()) if (c.id === id || c.shot === id) {
        const visual = { ...o };
        if (o.speed != null) c.speed = Number(o.speed) || 1;
        if (o.ramp != null) c.ramp = ['in','out','hit'].includes(o.ramp) ? o.ramp : null;
        delete visual.speed; delete visual.ramp; delete visual.slip;
        c.opts = visual;
      }
    }
  }
  function project() {
    if (!doc?.seq || !S) return;
    const payload = EEPreview.build();
    if (!payload) return;
    const e = payload.edit, fps = e.fps;
    const clips = doc.seq.video.flat();
    S.timeline = { ...S.timeline, fps, frames: e.frames, speechFrames: e.montageStart,
      speechStart: Math.max(0, doc.quote.lines[0]?.[0]?.start - 0.4 || 0), beats: e.beats,
      shots: e.shots.filter(c => !c.speech && !c.gap).map(c => ({ ...c, seqId:c.id, id:clips.find(x => x.id === c.id)?.shot || c.id })),
      error: ['video','audio'].some(kind => doc.seq[kind].some((track,i) => !(doc.seq.off || []).includes(`${kind === 'video' ? 'V' : 'A'}${i+1}`) && track.some(c => !c.off)))
        ? null : 'Add a clip or enable a track before exporting.' };
    S.sequenceCaptions = e.captions;
  }
  async function autoCut(update = null, ask = true) {
    if (running || starting || !await save()) return false;
    if (ask && !confirm('Rebuild the main video and audio tracks to the beat? Undo restores your current arrangement.')) return false;
    const next = structuredClone(doc);
    if (update) update(next);
    starting = true; status();
    const res = await post('seq/auto', {...next, keep:next.seq});
    starting = false;
    if (res.error) { toast(res.error, true); draw(); return false; }
    pick = {kind:null};
    change(d => { d.settings = next.settings; d.curation = next.curation; d.seq = res.seq; });
    setZoom();
    toast('Timeline recut to the beat. Undo restores your previous arrangement.');
    return true;
  }
  function finalShot(id) {
    const shot = S.shots.find(c => c.id === id);
    if (!shot) return;
    const old = doc.seq.video.flat().find(c => c.kind === 'hero');
    if (!old) return EETimeline.addShot(id,{at:total()});
    change(d => {
      const c = find(old.id, d);
      c.src = `montage:${S.sources.montage[shot.src]}`; c.shot = id;
      c.in = shot.start; c.ramp = null; c.speed = 1; delete c.rspan;
      d.curation.hero = id;
    });
    pick = {kind:'seq',id:old.id}; draw();
  }
  function setSpeech(d, quote) {
    const oldKey = `speech:${S.sources.speech[d.quote.clip]}`;
    const newKey = `speech:${S.sources.speech[quote.clip]}`;
    const a = Math.max(0, quote.lines[0][0].start - 0.4);
    const b = quote.lines.at(-1).at(-1).end + 0.5;
    for (const track of [...d.seq.video, ...d.seq.audio]) for (const c of track) {
      if (c.kind === 'speech' && c.src === oldKey) { c.src = newKey; c.in = a; c.dur = b - a; c.speed = 1; c.ramp = null; }
    }
    d.quote = {clip:quote.clip,lines:quote.lines};
  }
  function wordTime(word) {
    const source = `speech:${S.sources.speech[doc.quote.clip]}`;
    const matches = doc.seq.audio.flatMap((track,i) => (doc.seq.off || []).includes(`A${i+1}`) ? [] : track)
      .filter(c => !c.off && c.src === source && word.start >= c.in - 0.001 && word.start < c.in + c.dur * (c.speed || 1))
      .map(c => c.at + (word.start - c.in) / (c.speed || 1));
    return matches.sort((a,b) => Math.abs(a-now) - Math.abs(b-now))[0] ?? 0;
  }
  window.EESequence = {options,editOptions,syncChoices,project,autoCut,finalShot,setSpeech,wordTime};
  const originalTick = window.easyeditTick;
  EEPreview.configure({
    getDoc:()=>doc, getState:()=>S, getSlug:()=>slug,
    frame:()=>$('frame'),
    onPlayer(api) {
      player = api;
      if (!api) return;
      base = {clip:doc.quote.clip,speechStart:Math.max(0,doc.quote.lines[0]?.[0]?.start - 0.4 || 0),montage:0};
      api.volume(muted ? 0 : Number($('vol').value));
      api.seek(Math.min(now, Math.max(0,api.total - 0.001)));
      $('blank').style.display = 'none';
    },
    onTick:originalTick,
    onStatus({state,message,retry}) {
      $('blank').style.display = 'none';
      $('previewState').hidden = state === 'ready';
      $('previewState').querySelector('span').textContent = message || 'Loading preview…';
      $('previewRetry').hidden = !retry;
      if (state === 'ready' && S && doc) status();
    },
  });
  EETimeline.mount();
  $('apply').textContent = 'Render preview';
  $('apply').title = 'Render an exact preview with sound effects and depth. Timeline edits already play live.';
  $('fxReset').onclick = () => { if (pick.kind === 'seq') editOptions([pick.id], o => { for (const k of Object.keys(o)) delete o[k]; }); };
  $('kitHead').addEventListener('click', e => { if (e.target.closest('#kitClear') && pick.kind === 'seq') $('fxReset').click(); });
  $('rates').onclick = e => { const b = e.target.closest('[data-rate]'); if (b) autoCut(d => {d.settings.cut_rate=b.dataset.rate; delete d.curation.cuts;}); };
  $('ml').onchange = () => autoCut(d => {d.settings.montage_length=Number($('ml').value); delete d.curation.cuts;});
  $('hl').onchange = () => change(d => {
    const length = Number($('hl').value); d.settings.hero_length=length;
    for (const c of d.seq.video.flat()) if (c.kind === 'hero') c.dur=length;
  });
  $('velSeg').onclick = e => {
    const b=e.target.closest('[data-vel]'); if (!b) return;
    change(d => {d.settings.velocity=!!b.dataset.vel; d.seq.video.flat().forEach((c,i) => {if(c.kind !== 'speech') c.ramp=d.settings.velocity ? ['in','out','hit'][i%3] : null;});});
  };
  // Keep imported sources available to both the timeline and monitor.
  const originalLoadLib = loadLib;
  loadLib = async function() {
    await originalLoadLib();
    try { const media = await EEPreview.refreshMedia(); if (media) EETimeline.setMedia(media); }
    catch (error) { toast(error.message || 'Source media could not load.', true); }
  };
  const originalCloseSource = closeSource;
  closeSource = function() {originalCloseSource(); if(doc?.seq) $('blank').style.display='none';};
  const oldRun = run;
  run = async function(mode,label,body) {
    const ok = await oldRun(mode,label,body);
    if (ok && ['build','full','draft'].includes(mode)) await EEPreview.useRendered?.();
    if (ok && doc?.seq && (mode === 'clip' || mode === 'restore')) {
      const media = await EEPreview.refreshMedia(); EETimeline.setMedia(media || {});
      if(mode === 'clip' && body?.role === 'music') {
        const result = await post('seq/music',{seq:doc.seq});
        if(result.clip) change(d => { d.seq.audio=d.seq.audio.map(t=>t.filter(c=>c.kind !== 'music')); if(!d.seq.audio[1])d.seq.audio[1]=[]; d.seq.audio[1].push(result.clip); });
        else if(result.error) toast(result.error,true);
      }
      pushLive();
    }
    return ok;
  };
})();
