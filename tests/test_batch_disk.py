"""batch_edit's disk check must measure the jobs/ filesystem, not a hardcoded drive."""
import tempfile
import unittest
from collections import namedtuple
from pathlib import Path
from unittest import mock

import batch_edit

Usage = namedtuple("Usage", "total used free")


def _fake_usage(path, *args, **kwargs):
    if str(path) == "C:":
        raise FileNotFoundError("no C: drive on this machine")
    return Usage(total=10 * 10**9, used=4 * 10**9, free=6 * 10**9)


class DiskGbTest(unittest.TestCase):
    def test_reports_positive_free_space_on_this_machine(self):
        free = batch_edit.disk_gb()
        self.assertIsInstance(free, float)
        self.assertGreater(free, 0.0)

    def test_measures_the_filesystem_that_holds_jobs(self):
        with mock.patch.object(batch_edit.shutil, "disk_usage", side_effect=_fake_usage) as usage:
            self.assertEqual(batch_edit.disk_gb(), 6.0)
        usage.assert_called_once()
        measured = Path(usage.call_args.args[0])
        self.assertNotEqual(str(measured), "C:")
        self.assertTrue(measured.exists())
        self.assertTrue(batch_edit.JOBS.resolve().is_relative_to(measured.resolve()))

    def test_fresh_checkout_without_jobs_measures_nearest_existing_ancestor(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_jobs = Path(tmp) / "not" / "yet" / "jobs"
            with mock.patch.object(batch_edit, "JOBS", missing_jobs), \
                    mock.patch.object(batch_edit.shutil, "disk_usage", side_effect=_fake_usage) as usage:
                self.assertEqual(batch_edit.disk_gb(), 6.0)
            usage.assert_called_once()
            measured = Path(usage.call_args.args[0])
            self.assertEqual(measured.resolve(), Path(tmp).resolve())


if __name__ == "__main__":
    unittest.main()
