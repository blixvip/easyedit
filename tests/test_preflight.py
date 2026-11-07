"""The HyperFrames check must run in the CLI pre-flight, before any download or planning."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyedit import __main__ as cli
from easyedit import render

MISSING = Path("definitely/not/here/hyperframes.mjs")


class _Stream(io.StringIO):
    def reconfigure(self, **kwargs):  # main() reconfigures the std streams
        pass


class RendererPreflightTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        jobs = mock.patch.object(cli, "JOBS", Path(tmp.name))
        jobs.start()
        self.addCleanup(jobs.stop)
        self.cli_path = mock.patch.object(render, "CLI", MISSING)
        self.cli_path.start()
        self.addCleanup(self.cli_path.stop)
        self.make_plan = mock.patch.object(cli.planner, "make_plan",
                                           side_effect=RuntimeError("reached planning"))
        self.mock_plan = self.make_plan.start()
        self.addCleanup(self.make_plan.stop)

    def test_missing_renderer_exits_before_planning(self):
        err = _Stream()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            cli.main(["Gladiator", "--llm", "none"])
        self.assertEqual(cm.exception.code, 1)
        self.assertIn("hyperframes not installed: run `npm install` in the easyedit folder", err.getvalue())
        self.mock_plan.assert_not_called()

    def test_no_render_skips_the_renderer_check(self):
        with self.assertRaisesRegex(RuntimeError, "reached planning"):
            cli.main(["Gladiator", "--llm", "none", "--no-render"])
        self.mock_plan.assert_called_once()


if __name__ == "__main__":
    unittest.main()
