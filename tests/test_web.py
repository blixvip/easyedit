"""Regression tests for the local web API."""
import json
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyedit import sheet, web

SIGKILL = getattr(signal, "SIGKILL", 9)


class FinalOutputTest(unittest.TestCase):
    def test_title_containing_preview_keeps_its_final_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "sneak-preview"
            job.mkdir()
            final = job / "sneak-preview.mp4"
            final.touch()
            (job / "sneak-preview-preview.mp4").touch()
            self.assertEqual(web.output_of(job), final)

    def test_custom_output_containing_preview_is_not_the_preview(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "movie"
            job.mkdir()
            final = job / "review-preview-cut.mp4"
            final.touch()
            (job / "movie-preview.mp4").touch()
            self.assertEqual(web.output_of(job), final)

    def test_preview_only_has_no_final_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            job = Path(tmp) / "sneak-preview"
            job.mkdir()
            (job / "sneak-preview-preview.mp4").touch()
            self.assertIsNone(web.output_of(job))

    def test_final_sheet_uses_the_finished_video_for_a_preview_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            job = root / "sneak-preview"
            job.mkdir()
            final = job / "sneak-preview.mp4"
            final.touch()
            (job / "sneak-preview-preview.mp4").touch()
            with mock.patch.object(sheet, "JOBS", root), \
                    mock.patch.object(sheet.sys, "argv", ["easyedit.sheet", "Sneak Preview", "--final"]), \
                    mock.patch.object(sheet, "video_sheet", return_value=None) as video_sheet:
                sheet.main()
            video_sheet.assert_called_once_with(final, job / "qa" / "final.jpg", 24)


class FakeGroup:
    """Stands in for os.killpg: a process group that dies after N liveness checks or on SIGKILL."""

    def __init__(self, alive_polls=0, dies_on_sigkill=False):
        self.alive_polls = alive_polls
        self.dies_on_sigkill = dies_on_sigkill
        self.sent = []
        self.killed = False

    def killpg(self, pgid, sig):
        if sig == 0:
            if self.killed or self.alive_polls <= 0:
                raise ProcessLookupError()
            self.alive_polls -= 1
            return None
        self.sent.append((pgid, sig))
        if sig == SIGKILL and self.dies_on_sigkill:
            self.killed = True
        return None


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
        proc.pid = 4321
        proc.poll.return_value = None
        fh = mock.MagicMock()
        fh.closed = False
        web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": []}

        with mock.patch.object(web, "_POSIX", False, create=True), \
                mock.patch.object(web.subprocess, "run") as run:
            stopped = web.stop_job("test-slug")

        self.assertTrue(stopped)
        run.assert_called_once()
        self.assertIn("taskkill", run.call_args.args[0])
        proc.wait.assert_called_once_with(timeout=5)
        fh.close.assert_called_once()

    def test_stop_job_signals_process_group_on_posix(self):
        proc = mock.MagicMock()
        proc.pid = 4321
        proc.poll.return_value = None
        fh = mock.MagicMock()
        fh.closed = False
        web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": [], "pgid": 4321}
        group = FakeGroup(alive_polls=1)
        killpg = mock.MagicMock(side_effect=group.killpg)

        with mock.patch.object(web, "_POSIX", True, create=True), \
                mock.patch.object(web.os, "killpg", create=True, new=killpg):
            stopped = web.stop_job("test-slug")

        self.assertTrue(stopped)
        killpg.assert_any_call(4321, signal.SIGTERM)
        proc.terminate.assert_not_called()
        fh.close.assert_called_once()

    def test_stop_job_signals_group_even_when_parent_already_exited(self):
        proc = mock.MagicMock()
        proc.pid = 4321
        proc.poll.return_value = 0
        fh = mock.MagicMock()
        fh.closed = False
        web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": [], "pgid": 4321}
        group = FakeGroup(alive_polls=1)
        killpg = mock.MagicMock(side_effect=group.killpg)

        with mock.patch.object(web, "_POSIX", True, create=True), \
                mock.patch.object(web.os, "killpg", create=True, new=killpg):
            stopped = web.stop_job("test-slug")

        self.assertFalse(stopped)
        killpg.assert_any_call(4321, signal.SIGTERM)
        self.assertEqual(group.alive_polls, 0)
        fh.close.assert_called_once()

    def test_stop_job_escalates_to_sigkill_when_group_ignores_sigterm(self):
        proc = mock.MagicMock()
        proc.pid = 4321
        proc.poll.return_value = None
        fh = mock.MagicMock()
        fh.closed = False
        web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": [], "pgid": 4321}
        group = FakeGroup(alive_polls=10**6, dies_on_sigkill=True)

        with mock.patch.object(web, "_POSIX", True, create=True), \
                mock.patch.object(web, "STOP_GRACE_SECONDS", 0.0), \
                mock.patch.object(web.signal, "SIGKILL", SIGKILL, create=True), \
                mock.patch.object(web.os, "killpg", create=True, side_effect=group.killpg):
            web.stop_job("test-slug")

        self.assertEqual(group.sent, [(4321, signal.SIGTERM), (4321, SIGKILL)])

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

    def test_stop_job_never_signals_a_group_it_did_not_record(self):
        # A run without a recorded pgid (or a bogus one like 1) must not reach os.killpg:
        # a MagicMock pid converts to 1, and killpg(1, ...) once took down the CI runner.
        for pgid in (None, 0, 1, mock.MagicMock()):
            proc = mock.MagicMock()
            proc.poll.return_value = None
            fh = mock.MagicMock()
            fh.closed = False
            web._runs["test-slug"] = {"proc": proc, "fh": fh, "started": 0.0, "argv": [], "pgid": pgid}
            killpg = mock.MagicMock()
            with mock.patch.object(web, "_POSIX", True, create=True), \
                    mock.patch.object(web.os, "killpg", create=True, new=killpg):
                self.assertTrue(web.stop_job("test-slug"))
            killpg.assert_not_called()
            proc.terminate.assert_called_once()

    def test_signal_group_ignores_permission_error(self):
        with mock.patch.object(web.os, "killpg", create=True, side_effect=PermissionError):
            web._signal_group(4321, signal.SIGTERM)
            self.assertFalse(web._group_alive(4321))

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


class JobOptionValidationTest(unittest.TestCase):
    def test_valid_options_match_cli_domains(self):
        for body in (
            {"fps": 24, "llm": "auto", "montage_length": 1, "hero_length": 0.5},
            {"fps": "30", "llm": "claude", "montage_length": "21.5", "hero_length": "3.4"},
            {"fps": 60, "llm": "codex", "montage_length": 21, "hero_length": 3.4},
            {"llm": "none"},
        ):
            with self.subTest(body=body):
                web.validate_job_options(body)

    def test_invalid_fps_values_are_rejected(self):
        for value in ("abc", "24.0", 24.0, 25, 0, -30, True, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "^fps must be one of 24, 30, or 60$"):
                    web.validate_job_options({"fps": value})

    def test_invalid_llm_values_are_rejected(self):
        for value in ("gpt4", "GPT", "", True, [], {}):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "^llm must be one of auto, claude, codex, or none$"):
                    web.validate_job_options({"llm": value})

    def test_non_positive_and_non_finite_lengths_are_rejected(self):
        for field in ("montage_length", "hero_length"):
            for value in ("abc", 0, -5, float("inf"), float("-inf"), float("nan"), True, [], {}):
                with self.subTest(field=field, value=value):
                    with self.assertRaisesRegex(ValueError, f"^{field} must be a positive number$"):
                        web.validate_job_options({field: value})

    def test_api_returns_field_specific_400_without_starting_child(self):
        for body, message in (
            ({"movie": "X", "fps": "abc"}, "fps must be one of 24, 30, or 60"),
            ({"movie": "X", "llm": "gpt4"}, "llm must be one of auto, claude, codex, or none"),
            ({"movie": "X", "montage_length": "-5"}, "montage_length must be a positive number"),
            ({"movie": "X", "hero_length": "0"}, "hero_length must be a positive number"),
        ):
            with self.subTest(body=body), mock.patch.object(web.subprocess, "Popen") as popen:
                payload = json.dumps(body).encode()
                handler = mock.MagicMock()
                handler.path = "/api/new"
                handler.headers = {"Content-Length": str(len(payload))}
                handler.rfile.read.return_value = payload
                handler._json = mock.MagicMock()

                web.Handler.do_POST(handler)

                handler._json.assert_called_once_with({"error": message}, 400)
                popen.assert_not_called()

    def test_api_rejects_non_object_json_body(self):
        payload = b'["X"]'
        handler = mock.MagicMock()
        handler.path = "/api/new"
        handler.headers = {"Content-Length": str(len(payload))}
        handler.rfile.read.return_value = payload
        handler._json = mock.MagicMock()

        web.Handler.do_POST(handler)

        handler._json.assert_called_once_with({"error": "request body must be a JSON object"}, 400)


class StartJobSessionTest(unittest.TestCase):
    def tearDown(self):
        web._runs.clear()

    def test_start_job_puts_run_in_its_own_session_on_posix(self):
        jobs_root = Path(tempfile.mkdtemp()).resolve()
        with mock.patch.object(web, "JOBS", jobs_root), \
                mock.patch.object(web, "_POSIX", True, create=True), \
                mock.patch.object(web.subprocess, "Popen") as popen:
            web.start_job({"movie": "Test Movie"})

        self.assertTrue(popen.call_args.kwargs["start_new_session"])

    def test_stop_all_runs_when_server_exits(self):
        proc = mock.MagicMock()
        proc.poll.return_value = None
        web._runs["test-slug"] = {"proc": proc, "fh": mock.MagicMock(), "started": 0.0, "argv": []}
        server = mock.MagicMock()
        server.serve_forever.side_effect = KeyboardInterrupt

        with mock.patch.object(web, "setup_report"), \
                mock.patch.object(web, "ThreadingHTTPServer", return_value=server), \
                mock.patch.object(web, "stop_job") as stop, \
                mock.patch.object(web.sys, "argv", ["easyedit.web", "--no-browser"]):
            web.main()

        stop.assert_called_once_with("test-slug")


if __name__ == "__main__":
    unittest.main()
