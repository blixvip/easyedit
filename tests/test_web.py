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


if __name__ == "__main__":
    unittest.main()