"""Regression tests for the local web API."""
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


if __name__ == "__main__":
    unittest.main()