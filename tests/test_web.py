"""Local API regressions. Small temporary files; no downloads, models, or renders."""
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from easyedit import web


def request(path, body=b"", headers=None, method="POST"):
    handler = object.__new__(web.Handler)
    handler.path = path
    handler.command = method
    handler.headers = {"Content-Length": str(len(body)), **(headers or {})}
    handler.rfile = io.BytesIO(body)
    handler.wfile = io.BytesIO()
    handler.close_connection = False
    handler._json = lambda data, code=200: (code, data)
    return handler


class ApiInputTests(unittest.TestCase):
    def test_malformed_length_returns_an_error_without_reading(self):
        for length in ("bad", "-1"):
            with self.subTest(length=length):
                handler = request("/api/new", headers={"Content-Length": length})
                code, data = handler.do_POST()
                self.assertEqual(code, 400)
                self.assertIn("length", data["error"].lower())
                self.assertTrue(handler.close_connection)

    def test_json_must_be_an_object(self):
        for body in (b"[]", b"null", b'"movie"', b"23"):
            with self.subTest(body=body):
                code, data = request("/api/new", body).do_POST()
                self.assertEqual(code, 400)
                self.assertEqual(data["error"], "JSON body must be an object")

    def test_invalid_utf8_returns_json_error(self):
        code, data = request("/api/new", b"\xff").do_POST()
        self.assertEqual(code, 400)
        self.assertIn("JSON", data["error"])

    def test_unknown_project_log_is_not_a_queued_job(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(web, "JOBS", Path(tmp)):
            code, data = request("/api/log/missing", method="GET").do_GET()
            self.assertEqual((code, data), (404, {"error": "unknown edit"}))

    def test_bad_create_options_do_not_start_a_worker(self):
        invalid = [{"movie": 12}, {"movie": "Film", "montage": "https://example.com"},
                   {"movie": "Film", "montage": [12]}, {"movie": "Film", "fps": 25},
                   {"movie": "Film", "hero_length": -1}, {"movie": "Film", "llm": "bad"}]
        with patch.object(web, "_launch") as launch:
            for body in invalid:
                with self.subTest(body=body), self.assertRaises(ValueError):
                    web.start_job(body)
            launch.assert_not_called()


class FileRangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / "video.mp4"
        self.file.write_bytes(b"0123456789")

    def serve(self, byte_range, method="GET"):
        handler = request("/media/job/video.mp4", headers={"Range": byte_range}, method=method)
        response = {"headers": {}}
        handler.send_response = lambda code: response.update(code=code)
        handler.send_header = lambda name, value: response["headers"].update({name: value})
        handler.end_headers = lambda: None
        handler._file(self.file)
        return response, handler.wfile.getvalue()

    def test_suffix_range_is_the_end_of_the_video(self):
        response, body = self.serve("bytes=-3")
        self.assertEqual(response["code"], 206)
        self.assertEqual(response["headers"]["Content-Range"], "bytes 7-9/10")
        self.assertEqual(body, b"789")

    def test_unsatisfiable_and_malformed_ranges_are_rejected(self):
        for value in ("bytes=20-", "bytes=8-2", "bytes=-0", "bytes=-", "bytes=x-y"):
            with self.subTest(value=value):
                response, body = self.serve(value)
                self.assertEqual(response["code"], 416)
                self.assertEqual(response["headers"]["Content-Range"], "bytes */10")
                self.assertEqual(body, b"")

    def test_open_range_and_head_have_correct_lengths(self):
        response, body = self.serve("bytes=4-")
        self.assertEqual(body, b"456789")
        self.assertEqual(response["headers"]["Content-Length"], "6")
        response, body = self.serve("bytes=4-", method="HEAD")
        self.assertEqual(response["headers"]["Content-Length"], "6")
        self.assertEqual(body, b"")


class ProjectFilesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.job = self.root / "movie"
        self.job.mkdir()
        jobs = patch.object(web, "JOBS", self.root)
        jobs.start()
        self.addCleanup(jobs.stop)

    def test_same_size_uploads_with_same_name_keep_both_files(self):
        path = "/api/edit/movie/clips/upload?name=clip.mp4"
        code, first = request(path, b"first").do_POST()
        self.assertEqual(code, 200)
        handler = request(path, b"other")
        code, second = handler.do_POST()
        self.assertEqual(code, 200)
        self.assertEqual(handler.rfile.tell(), 5)
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(Path(first["path"]).read_bytes(), b"first")
        self.assertEqual(Path(second["path"]).read_bytes(), b"other")

    def test_incomplete_upload_does_not_replace_existing_file(self):
        target = self.job / "downloads/uploads/clip.mp4"
        target.parent.mkdir(parents=True)
        target.write_bytes(b"existing")
        code, data = request("/api/edit/movie/clips/upload?name=clip.mp4", b"short",
                             {"Content-Length": "10"}).do_POST()
        self.assertEqual(code, 400)
        self.assertIn("whole", data["error"])
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(list(target.parent.glob("*.part")), [])

    def test_history_preserves_multiple_changes_in_one_second(self):
        choices = self.job / "plan.json"
        with patch.object(web.time, "strftime", return_value="20261003-120000"):
            choices.write_text('{"title":"First"}')
            web.snapshot(self.job, "First")
            choices.write_text('{"title":"Second"}')
            web.snapshot(self.job, "Second")
        versions = web.history(self.job)["versions"]
        self.assertEqual(len(versions), 2)
        self.assertNotEqual(versions[0]["id"], versions[1]["id"])
        self.assertEqual({json.loads(p.read_text())["title"] for p in self.job.glob("history/*/plan.json")},
                         {"First", "Second"})

    def test_history_same_second_restore_keeps_current_and_restores_old(self):
        (self.job / "sources.json").write_text('{"speech":[]}')
        choices = self.job / "plan.json"
        with patch.object(web.time, "strftime", return_value="20261003-120000"):
            choices.write_text('{"title":"First"}')
            web.snapshot(self.job, "First")
            stamp = web.history(self.job)["versions"][0]["id"]
            choices.write_text('{"title":"Second"}')
            with patch.object(web.editor, "state", return_value={"built": {}}):
                web.go_back(self.job, stamp)
        self.assertEqual(json.loads(choices.read_text())["title"], "First")
        self.assertEqual(len(web.history(self.job)["versions"]), 2)

    def test_restoring_oldest_version_survives_history_retention(self):
        (self.job / "sources.json").write_text('{"speech":[]}')
        choices = self.job / "plan.json"
        with patch.object(web.time, "strftime", return_value="20261003-120000"):
            for i in range(20):
                choices.write_text(json.dumps({"title": str(i)}))
                web.snapshot(self.job, str(i))
            stamp = web.history(self.job)["versions"][-1]["id"]
            choices.write_text('{"title":"Current"}')
            with patch.object(web.editor, "state", return_value={"built": {}}):
                web.go_back(self.job, stamp)
        self.assertEqual(json.loads(choices.read_text())["title"], "0")
        self.assertLessEqual(len(web.history(self.job)["versions"]), 20)

    def test_new_cannot_overwrite_existing_project(self):
        (self.job / "plan.json").write_text('{"title":"Original"}')
        with patch.object(web, "_launch") as launch:
            code, data = request("/api/new", b'{"movie":"Movie"}').do_POST()
        self.assertEqual(code, 409)
        self.assertEqual(data["slug"], "movie")
        self.assertTrue(data["exists"])
        self.assertEqual(json.loads((self.job / "plan.json").read_text())["title"], "Original")
        launch.assert_not_called()

    def test_retry_targets_original_slug_after_project_title_changes(self):
        (self.job / "plan.json").write_text('{"title":"Renamed"}')
        (self.job / "sources.json").write_text('{"specs":{},"speech":[],"montage":[],"music":[]}')
        with patch.object(web, "_launch") as launch:
            code, data = request("/api/new", b'{"movie":"Renamed","resume":"movie"}').do_POST()
        self.assertEqual(code, 200)
        self.assertEqual(data["slug"], "movie")
        self.assertEqual(launch.call_args.args[0], "movie")
        self.assertEqual(launch.call_args.args[1][4], "movie")

    def test_browser_fps_string_can_start_a_project(self):
        with patch.object(web, "_launch") as launch:
            code, data = request("/api/new", b'{"movie":"Movie","fps":"30"}').do_POST()
        self.assertEqual(code, 200)
        args = launch.call_args.args[1]
        self.assertEqual(args[args.index("--fps") + 1], "30")

    def test_log_exposes_failed_worker_even_without_error_keyword(self):
        (self.job / "run.log").write_text("downloading footage\nprovider unavailable\n")
        proc = Mock()
        proc.poll.return_value = 1
        with patch.dict(web._runs, {"movie": {"proc": proc, "fh": io.StringIO()}}, clear=True):
            code, data = request("/api/log/movie", method="GET").do_GET()
        self.assertEqual(code, 200)
        self.assertFalse(data["running"])
        self.assertEqual(data["exitCode"], 1)
        self.assertTrue(data["error"])

    def test_save_cannot_change_choices_while_a_worker_is_reading_them(self):
        proc = Mock()
        proc.poll.return_value = None
        with patch.dict(web._runs, {"movie": {"proc": proc}}, clear=True), \
                patch.object(web.editor, "save") as save:
            code, data = request("/api/edit/movie/save", b'{"plan":{"title":"Changed"}}').do_POST()
        self.assertEqual(code, 409)
        self.assertIn("finish", data["error"])
        save.assert_not_called()

    def test_failed_worker_launch_releases_its_log_handle(self):
        with patch.object(web.subprocess, "Popen", side_effect=OSError("unavailable")):
            with self.assertRaises(OSError):
                web._launch("movie", ["missing"])
        # Windows cannot remove this while the failed launch still holds an open handle.
        (self.job / "run.log").unlink()
        self.assertNotIn("movie", web._runs)

    def test_delete_does_not_report_success_when_files_are_locked(self):
        (self.job / "plan.json").write_text('{}')
        def locked(path, ignore_errors=False):
            if not ignore_errors:
                raise PermissionError("file is open")
        with patch.object(web.shutil, "rmtree", side_effect=locked):
            code, data = request("/api/delete", b'{"slug":"movie"}').do_POST()
        self.assertEqual(code, 400)
        self.assertEqual(data["error"], "file is open")
        self.assertTrue(self.job.exists())

    def test_delete_waits_for_its_worker_before_removing_files(self):
        proc = Mock()
        proc.poll.return_value = None
        proc.wait.side_effect = subprocess.TimeoutExpired("worker", 5)
        fh = io.StringIO()
        with patch.dict(web._runs, {"movie": {"proc": proc, "fh": fh}}, clear=True), \
                patch.object(web, "stop_job", return_value=True):
            code, data = request("/api/delete", b'{"slug":"movie"}').do_POST()
            self.assertIn("movie", web._runs)
        self.assertEqual(code, 409)
        self.assertTrue(self.job.exists())
        self.assertFalse(fh.closed)
        self.assertIn("stopping", data["error"])


if __name__ == "__main__":
    unittest.main()
