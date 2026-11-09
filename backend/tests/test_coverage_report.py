"""P1-b — résumé et comparaison de couverture (scripts/coverage_report.py) : informatif, jamais bloquant sur un pourcentage."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "coverage_report.py"
spec = importlib.util.spec_from_file_location("coverage_report", SCRIPT)
cr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cr)


def file(statements, covered, branches=0, covered_branches=0):
    return {
        "summary": {
            "num_statements": statements,
            "covered_lines": covered,
            "num_branches": branches,
            "covered_branches": covered_branches,
        }
    }


REPORT = {
    "meta": {"version": "7.17.2"},
    "files": {
        "runs/engine.py": file(100, 80, 20, 10),
        "runs/services.py": file(50, 50),
        "ai/jobs.py": file(10, 0),
        "manage_x.py": file(4, 4),
    },
}


def test_groups_by_first_folder_and_counts_lines_and_branches_together():
    s = cr.summarize(REPORT)
    runs = s["packages"]["runs"]
    assert (runs["statements"], runs["covered"], runs["branches"], runs["covered_branches"]) == (150, 130, 20, 10)
    assert runs["percent"] == round(100 * 140 / 170, 2)  # (130 + 10) / (150 + 20)
    assert s["packages"]["ai"]["percent"] == 0.0
    assert s["packages"]["(racine)"]["statements"] == 4
    assert s["total"]["statements"] == 164 and s["total"]["covered"] == 134


def test_an_empty_report_is_not_a_division_by_zero():
    assert cr.summarize({"files": {}})["total"]["percent"] == 100.0


def test_compare_reports_the_gap_and_marks_new_packages():
    base = cr.summarize(REPORT)
    better = cr.summarize({"files": {**REPORT["files"], "ai/jobs.py": file(10, 10), "new/x.py": file(5, 5)}})
    rows = {name: (before, now, diff) for name, before, now, diff in cr.compare(base, better)}
    assert rows["ai"] == (0.0, 100.0, 100.0)
    assert rows["new"][0] is None and rows["new"][2] is None
    assert rows["runs"][2] == 0.0
    assert rows["TOTAL"][2] > 0


def test_never_fails_on_a_lower_percentage_and_writes_only_after_a_full_green_run(tmp_path, capsys):
    report, base = tmp_path / "c.json", tmp_path / "base.json"
    report.write_text(json.dumps(REPORT))
    assert cr.main([str(report), "--baseline", str(base), "--commit", "abc", "--pytest-exit", "1", "--write"]) == 1
    assert not base.exists()  # a failing suite under-measures: nothing is written
    assert (
        cr.main(
            [
                str(report),
                "--baseline",
                str(base),
                "--commit",
                "abc",
                "--python",
                "3.13.1",
                "--pytest-exit",
                "0",
                "--write",
            ]
        )
        == 0
    )
    written = json.loads(base.read_text())
    assert written["commit"] == "abc" and written["total"]["statements"] == 164 and written["python"] == "3.13.1"
    assert written["coverage_version"] == "7.17.2"  # the version of the coverage tool, not of Python
    worse = tmp_path / "w.json"
    worse.write_text(json.dumps({"files": {"runs/engine.py": file(100, 1)}}))
    assert cr.main([str(worse), "--baseline", str(base)]) == 0  # lower coverage: reported, never an error
    assert "écart" in capsys.readouterr().out


JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="6">
<testcase classname="t.a" name="ok"/>
<testcase classname="t.a" name="bad"><failure message="boom">x</failure></testcase>
<testcase classname="t.a" name="err"><error message="setup">x</error></testcase>
<testcase classname="t.b" name="s1"><skipped type="pytest.skip" message="docker  absent">t.py:1: docker absent</skipped></testcase>
<testcase classname="t.b" name="s2"><skipped type="pytest.skip" message="docker absent"/></testcase>
<testcase classname="t.b" name="x1"><skipped type="pytest.xfail" message="known"/></testcase>
</testsuite></testsuites>"""


def test_junit_summary_separates_skips_from_xfail_and_groups_reasons(tmp_path):
    path = tmp_path / "j.xml"
    path.write_text(JUNIT, encoding="utf-8")
    t = cr.summarize_tests(str(path))
    assert (t["tests"], t["passed"], t["failed"], t["skipped"], t["xfailed"]) == (6, 1, 2, 2, 1)
    assert t["skip_reasons"] == {"docker absent": 2}  # whitespace normalised, same reason grouped, xfail not a skip


def test_a_skip_without_message_is_still_traced(tmp_path):
    path = tmp_path / "j.xml"
    path.write_text(
        '<testsuites><testsuite><testcase name="a"><skipped type="pytest.skip"/></testcase></testsuite></testsuites>'
    )
    assert cr.summarize_tests(str(path))["skip_reasons"] == {"(sans raison)": 1}


def test_omissions_are_read_from_the_real_coverage_configuration():
    rc = Path(__file__).resolve().parents[1] / ".coveragerc"
    o = cr.read_omissions(str(rc))
    assert "tests/*" in o["omit"] and "*/migrations/*" in o["omit"]  # what is left out of the measure stays visible
    assert "if TYPE_CHECKING:" in o["exclude_also"]


def test_the_baseline_records_skips_omissions_and_file_count_and_a_missing_junit_is_said(tmp_path, capsys):
    report, base, junit = tmp_path / "c.json", tmp_path / "b.json", tmp_path / "j.xml"
    report.write_text(json.dumps(REPORT))
    junit.write_text(JUNIT, encoding="utf-8")
    rc = Path(__file__).resolve().parents[1] / ".coveragerc"
    args = [str(report), "--baseline", str(base), "--junit", str(junit), "--coveragerc", str(rc)]
    assert cr.main([*args, "--pytest-exit", "0", "--write", "--python", "3.13.1"]) == 0
    doc = json.loads(base.read_text())
    assert doc["files_measured"] == 4 and doc["tests"]["skipped"] == 2 and "tests/*" in doc["omissions"]["omit"]
    capsys.readouterr()
    assert cr.main([str(report), "--baseline", str(base), "--python", "3.14.4"]) == 0  # no junit, other interpreter
    out = capsys.readouterr().out
    assert "non journalisés" in out and "3.13.1 → 3.14.4" in out


def test_a_subset_measure_is_flagged_and_never_written_as_baseline(tmp_path, capsys):
    report, base = tmp_path / "c.json", tmp_path / "b.json"
    report.write_text(json.dumps(REPORT))
    assert cr.main([str(report), "--baseline", str(base), "--subset", "--write"]) == 1
    assert not base.exists()
    assert "MESURE PARTIELLE" in capsys.readouterr().out
    assert cr.main([str(report), "--baseline", str(base)]) == 0  # a full measure carries no such warning
    assert "MESURE PARTIELLE" not in capsys.readouterr().out
