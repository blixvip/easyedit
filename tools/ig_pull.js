// Pull a creator's Instagram reels through a signed-in browser (Aside REPL / any Playwright page context).
//
// yt-dlp's Instagram extractor is broken (2026) and Chrome's cookie DB is locked while Chrome runs, so the
// reels are read from the reel pages' own embedded JSON and handed to a tiny local receiver:
//
//   1. python -m easyedit.style pull <handle> --receive        (writes styles/<handle>/reels/<code>.mp4)
//   2. In the Aside REPL: paste this file, set HANDLE, run  await igPullAll()  (or igPullBatch(from, to))
//   3. python -m easyedit.style analyze <handle>
//
// REPL calls time out after 120 s, so pull in batches of 5-6 reels per call: igPullBatch(0, 6), (6, 12), ...
// Nothing but the public mp4 is fetched; CDN URLs are signed and expire, so re-run a batch if a PUT fails.

const HANDLE = 'nio_trade';
const RECEIVER = 'http://127.0.0.1:4477/';
const zz = ms => new Promise(r => setTimeout(r, ms));

async function igCodes() {
  const pg = await openTab(`https://www.instagram.com/${HANDLE}/reels/`);
  await zz(3000);
  const seen = new Set();
  let stale = 0;
  for (let i = 0; i < 60 && stale < 4; i++) {
    const links = await pg.evaluate(() => [...document.querySelectorAll('a[href*="/reel/"]')].map(a => a.getAttribute('href')));
    const before = seen.size;
    links.forEach(h => { const m = h.match(/\/reel\/([^/]+)\//); if (m) seen.add(m[1]); });
    stale = seen.size === before ? stale + 1 : 0;
    await pg.evaluate(() => window.scrollBy(0, document.documentElement.clientHeight * 2));
    await zz(1500);
  }
  await closeTab(pg);
  globalThis.IG_CODES = [...seen];
  console.log(`${seen.size} reels`);
  return globalThis.IG_CODES;
}

async function igReelInfo(pg, code) {
  await pg.goto(`https://www.instagram.com/reel/${code}/`, { waitUntil: 'domcontentloaded' });
  await zz(2500);
  return await pg.evaluate(() => {
    const html = [...document.scripts].map(s => s.textContent).join('\n');
    const m = html.match(/"video_versions":\s*(\[\{.*?\}\])/);
    let url = null, w = null, h = null;
    if (m) { try { const vv = JSON.parse(m[1]); url = vv[0].url; w = vv[0].width; h = vv[0].height; } catch (e) {} }
    let efg = {};
    try { efg = JSON.parse(atob(decodeURIComponent(url.match(/efg=([^&]+)/)[1]))); } catch (e) {}
    const d = document.querySelector('meta[property="og:description"]')?.content || '';
    return {
      url, w, h, dur: efg.duration_s ?? null,
      likes: (d.match(/([\d,.]+K?) likes/) || [])[1] || null,
      date: (d.match(/on ([A-Z][a-z]+ \d+, \d{4})/) || [])[1] || null,
      cap: ((d.match(/: "([\s\S]*)"\.\s*$/) || [])[1] || '').slice(0, 120) || null,
    };
  });
}

async function igPullBatch(from, to) {
  const codes = (globalThis.IG_CODES || await igCodes()).slice(from, to);
  const pg = await openTab('about:blank');
  const rows = [];
  const t0 = Date.now();
  for (const c of codes) {
    if (Date.now() - t0 > 95000) { rows.push(`${c} skipped(time)`); continue; }
    try {
      const inf = await igReelInfo(pg, c);
      if (!inf.url) { rows.push(`${c} no-url`); continue; }
      const r = await fetch(inf.url);
      const ab = await r.arrayBuffer();
      const p = await fetch(`${RECEIVER}${c}.mp4`, { method: 'PUT', body: Buffer.from(ab) });
      await fetch(`${RECEIVER}${c}.json`, { method: 'PUT', body: JSON.stringify({ ...inf, url: undefined, bytes: ab.byteLength }) });
      rows.push([c, inf.dur, `${inf.w}x${inf.h}`, ab.byteLength, p.status].join(' '));
    } catch (e) { rows.push(`${c} ERR ${String(e).slice(0, 80)}`); }
    await zz(1500);
  }
  await closeTab(pg);
  console.log(rows.join('\n'));
  return rows;
}

async function igPullAll(step = 5) {
  const codes = globalThis.IG_CODES || await igCodes();
  for (let i = 0; i < codes.length; i += step) await igPullBatch(i, i + step);
}
