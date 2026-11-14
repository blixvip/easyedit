"""Regression tests for the batch QA report gate."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import batch_edit


def _shot_row(index, marker, flags=""):
    return (
        f"{index:3d}{marker} {'shot-id':24s} {2.0:5.2f} "
        f"{0.2:+6.2f} {'Y' if index % 2 == 0 else '-':4s} {flags}"
    )


def _run_qa(report):
    with tempfile.TemporaryDirectory() as tmp:
        job = Path(tmp) / "sample"
        qa = job / "qa"
        qa.mkdir(parents=True)
        (qa / "report.txt").write_text(report, encoding="utf-8")
        with mock.patch.object(batch_edit, "JOBS", Path(tmp)), \
                mock.patch.object(batch_edit, "run", return_value=True):
            return batch_edit.qa_and_curate("sample")


class QaAndCurateTest(unittest.TestCase):
    def test_flagged_rows_count_toward_review_threshold(self):
        report = "\n".join([
            "300 men stood outside the station",
            "idx  id                        len   score face  flags",
            _shot_row(0, "*"),
            _shot_row(1, "x", "short"),
            _shot_row(2, "x", "title-card?"),
            _shot_row(3, " "),
            _shot_row(4, "*"),
            _shot_row(5, "x", "short,repeat"),
            _shot_row(105, "x", "repeat"),
        ])
        self.assertEqual(batch_edit._qa_shot_counts(report), (7, 3))
        self.assertEqual(_run_qa(report), "review")

    def test_picked_and_flagged_only_pool_is_not_mistaken_for_empty(self):
        report = "\n".join([
            "idx  id                        len   score face  flags",
            _shot_row(0, "*"),
            _shot_row(1, "x", "short"),
            _shot_row(105, "*"),
        ])
        self.assertEqual(batch_edit._qa_shot_counts(report), (3, 0))
        self.assertEqual(_run_qa(report), "ok")

    def test_numeric_quote_lines_before_table_are_not_counted(self):
        report = "\n".join([
            "  300 men stood outside the station",
            "idx  id                        len   score face  flags",
            _shot_row(105, "x", "short"),
        ])
        self.assertEqual(batch_edit._qa_shot_counts(report), (1, 0))

    def test_report_without_shot_rows_requires_review(self):
        self.assertEqual(_run_qa("QUOTE: ready\nSHOTS: no analysis yet"), "review")


if __name__ == "__main__":
    unittest.main()
