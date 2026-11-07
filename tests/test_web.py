"""Regression tests for the local web API."""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyedit import web


class RevealJobTest(unittest.TestCase):
    def test_reveal_job_uses_linux_file_manager(self):
        job = Path(tempfile.mkdtemp())
        with mock.patch.object(web.os, "name", "posix"), \
                mock.patch.object(web.sys, "platform", "linux"), \
                mock.patch.object(web.shutil, "which", return_value="/usr/bin/xdg-open"), \
                mock.patch.object(web.subprocess, "Popen") as popen:
            result = web.reveal_job(job)

        self.assertEqual(result, {"ok": True})
        popen.assert_called_once_with(["/usr/bin/xdg-open", str(job)])

    def test_reveal_job_uses_macos_file_manager(self):
        job = Path(tempfile.mkdtemp())
        with mock.patch.object(web.os, "name", "posix"), \
                mock.patch.object(web.sys, "platform", "darwin"), \
                mock.patch.object(web.subprocess, "Popen") as popen:
            result = web.reveal_job(job)

        self.assertEqual(result, {"ok": True})
        popen.assert_called_once_with(["open", str(job)])

    def test_reveal_job_returns_failure_when_launcher_is_missing(self):
        job = Path(tempfile.mkdtemp())
        with mock.patch.object(web.os, "name", "posix"), \
                mock.patch.object(web.sys, "platform", "linux"), \
                mock.patch.object(web.shutil, "which", return_value=None):
            result = web.reveal_job(job)

        self.assertEqual(result, {"ok": False, "error": "no file manager found"})


class StopJobTest(unittest.TestCase):
    def tearDown(self):
        web._runs.clear()

    def test_stop_job_closes_fh_and_waits_for_running_proc(self):
        proc = mock.MagicMock()
        proc.poll.return_value = None
        fh = mock.MagicMock()
        fh.closed = False
        web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": []}

        stopped = web.stop_job("test-slug")

        self.assertTrue(stopped)
        proc.terminate.assert_called_once()
        proc.wait.assert_called_once_with(timeout=5)
        fh.close.assert_called_once()

    def test_stop_job_closes_fh_for_already_finished_proc(self):
        proc = mock.MagicMock()
        proc.poll.return_value = 0
        fh = mock.MagicMock()
        fh.closed = False
        web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": []}

        stopped = web.stop_job("test-slug")

        self.assertFalse(stopped)
        proc.terminate.assert_not_called()
        fh.close.assert_called_once()

    def test_stop_job_nonexistent_slug(self):
        self.assertFalse(web.stop_job("nonexistent"))


class DeleteJobTest(unittest.TestCase):
    def tearDown(self):
        web._runs.clear()

    def test_delete_job_removes_runs_entry_and_closes_fh(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            jobs_root = Path(tmp_dir).resolve()
            job = jobs_root / "test-slug"
            job.mkdir(parents=True)
            log = job / "run.log"
            log.write_text("hello", encoding="utf-8")

            proc = mock.MagicMock()
            proc.poll.return_value = 0
            fh = mock.MagicMock()
            fh.closed = False
            web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": []}

            with mock.patch.object(web, "JOBS", jobs_root):
                payload = b'{"slug": "test-slug"}'
                handler = mock.MagicMock()
                handler.path = "/api/delete"
                handler.headers = {"Content-Length": str(len(payload))}
                handler.rfile.read.return_value = payload
                handler._json = mock.MagicMock()

                web.Handler.do_POST(handler)

                self.assertNotIn("test-slug", web._runs)
                fh.close.assert_called()
                self.assertFalse(job.exists())
                handler._json.assert_called_with({"deleted": True})

    def test_delete_job_returns_400_when_folder_still_exists(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            jobs_root = Path(tmp_dir).resolve()
            job = jobs_root / "test-slug"
            job.mkdir(parents=True)

            with mock.patch.object(web, "JOBS", jobs_root), \
                    mock.patch.object(web.shutil, "rmtree"):
                payload = b'{"slug": "test-slug"}'
                handler = mock.MagicMock()
                handler.path = "/api/delete"
                handler.headers = {"Content-Length": str(len(payload))}
                handler.rfile.read.return_value = payload
                handler._json = mock.MagicMock()

                web.Handler.do_POST(handler)

                handler._json.assert_called_with({"deleted": False}, 400)


class StartJobTest(unittest.TestCase):
    def tearDown(self):
        web._runs.clear()

    def test_start_job_closes_previous_fh(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            jobs_root = Path(tmp_dir)
            old_proc = mock.MagicMock()
            old_proc.poll.return_value = 0
            old_fh = mock.MagicMock()
            old_fh.closed = False
            web._runs["test-movie"] = {"proc": old_proc, "fh": old_fh, "started": 0.0, "argv": []}

            new_proc = mock.MagicMock()
            new_proc.poll.return_value = None

            with mock.patch.object(web, "JOBS", jobs_root), \
                    mock.patch.object(web.subprocess, "Popen", return_value=new_proc):
                res = web.start_job({"movie": "test movie"})

                self.assertTrue(res.get("started"))
                old_fh.close.assert_called()


class JobInfoTest(unittest.TestCase):
    def tearDown(self):
        web._runs.clear()

    def test_job_info_closes_fh_when_process_finished(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            jobs_root = Path(tmp_dir).resolve()
            job = jobs_root / "test-slug"
            job.mkdir(parents=True)

            proc = mock.MagicMock()
            proc.poll.return_value = 0
            fh = mock.MagicMock()
            fh.closed = False
            web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": []}

            info = web.job_info(job)

            self.assertFalse(info["running"])
            fh.close.assert_called_once()


class ThumbnailAccessibilityTest(unittest.TestCase):
    def test_gallery_markup_is_keyboard_and_screen_reader_friendly(self):
        page = (Path(__file__).resolve().parent.parent / "easyedit" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<button class="thumb" style="${thumb}" data-play=', page)
        self.assertIn('aria-label="Play ${esc(job.title)}"', page)
        self.assertIn('role="progressbar"', page)
        self.assertIn('aria-valuenow="${pct}"', page)
        self.assertIn('id="runs" aria-live="polite"', page)
        # Enter/Space on a native button fire a click, so the delegated handler must match buttons.
        self.assertIn("closest('button, [data-play]')", page)


FOCUS_HARNESS = r"""
const fs = require('fs');
const PAGE = __PAGE__;
const page = fs.readFileSync(PAGE, 'utf8');
// The real jobs state, focusKey, restoreFocus and refresh() from the page; only the DOM and api are stubbed.
const src = page.slice(page.indexOf('let jobs = [];'), page.indexOf('async function pullLog('));

// tile()/runCard() stubs return a delimited blob so that the page's join('') still works.
const encode = rows => '\u0001' + JSON.stringify(rows) + '\u0001';

class El {
  constructor(scope, attrs, text) {
    this.scope = scope;
    this.attributes = Object.entries(attrs).map(([name, value]) => ({ name, value }));
    this.textContent = text;
  }
  getAttribute(n) { const a = this.attributes.find(x => x.name === n); return a ? a.value : null; }
  closest(sel) { return sel.includes('#' + this.scope.id) ? this.scope : null; }
  focus() { doc.active = this; }
}
class Scope {
  constructor(id) { this.id = id; this.children = []; }
  // Assigning innerHTML replaces every child, as a browser does; focus on a replaced child is lost.
  set innerHTML(html) {
    this.children = String(html).split('\u0001').filter(Boolean)
      .flatMap(s => JSON.parse(s).map(([a, t]) => new El(this, a, t)));
    if (doc.active && doc.active.scope === this && !this.children.includes(doc.active)) doc.active = null;
  }
  querySelectorAll(sel) {
    const attr = sel.match(/\[([\w-]+)\]/)[1];
    return this.children.filter(e => e.getAttribute(attr) !== null);
  }
}
const runs = new Scope('runs'), grid = new Scope('grid');
const byId = { runs, grid, runsWrap: { hidden: false }, empty: { hidden: false } };
const doc = {
  active: null,
  get activeElement() { return this.active; },
  querySelectorAll(sel) {
    const m = sel.match(/^#(\w+) \[([\w-]+)\]$/);
    return m ? byId[m[1]].querySelectorAll('[' + m[2] + ']') : [];
  },
};

// Render `first`, focus the button at focusIndex in the grid, then refresh with `next` and report focus.
async function run(first, next, focusIndex) {
  runs.children = []; grid.children = []; doc.active = null;
  let jobs = first;
  const env = {
    document: doc,
    $: id => byId[id] || null,
    api: async () => ({ jobs }),
    tile: job => encode(job.buttons),
    runCard: job => encode(job.buttons),
    openLogs: new Set(),
    pullLog: async () => {},
  };
  const { refresh } = new Function(...Object.keys(env), src + '; return { refresh };')(...Object.values(env));
  await refresh();
  doc.active = grid.children[focusIndex];
  jobs = next;
  await refresh();
  const now = doc.active;
  return now ? { attr: now.attributes[0].name + '=' + now.attributes[0].value, label: now.textContent } : null;
}

(async () => {
  try {
    const out = {};
    // A Show/Hide log label change between refreshes must not drop focus.
    out.label = await run(
      [{ buttons: [[{ 'data-log': 'b' }, 'Show log'], [{ 'data-delete': 'b' }, 'Delete']] }],
      [{ buttons: [[{ 'data-log': 'b' }, 'Hide log'], [{ 'data-delete': 'b' }, 'Delete']] }],
      0);
    // Two buttons share data-play (thumbnail and Play): focus on the second must come back to the second.
    const play = [[{ 'data-play': 'b' }, ''], [{ 'data-play': 'b' }, 'Play']];
    out.dup = await run([{ buttons: play }], [{ buttons: play }], 1);
    console.log(JSON.stringify(out));
  } catch (e) {
    console.log(JSON.stringify({ error: String((e && e.stack) || e) }));
  }
})();
"""


class RefreshFocusTest(unittest.TestCase):
    def test_refresh_restores_focus_after_rerender(self):
        html = Path(__file__).resolve().parent.parent / "easyedit" / "web" / "index.html"
        script = FOCUS_HARNESS.replace("__PAGE__", json.dumps(str(html)))
        proc = subprocess.run(["node", "-"], input=script, capture_output=True, text=True, timeout=60)
        lines = proc.stdout.strip().splitlines()
        result = json.loads(lines[-1]) if lines else {"error": proc.stderr}
        self.assertNotIn("error", result, f"refresh must restore keyboard focus (harness error): {result}")
        self.assertEqual(result.get("label"), {"attr": "data-log=b", "label": "Hide log"},
                         f"refresh must restore keyboard focus to the re-rendered button: {result}")
        self.assertEqual(result.get("dup"), {"attr": "data-play=b", "label": "Play"},
                         f"refresh must restore keyboard focus to the same-attribute button: {result}")


if __name__ == "__main__":
    unittest.main()
