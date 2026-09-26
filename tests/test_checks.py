"""The broken sample must trip every check once; the healthy one must be clean."""
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from pbip_guardrails.checks import run_checks
from pbip_guardrails.cli import main
from pbip_guardrails.publish import safe_copy

ROOT = Path(__file__).resolve().parents[1] / "examples"
FAMILY = r"Segment (\d+)"


@pytest.fixture(scope="session", autouse=True)
def samples():
    subprocess.run([sys.executable, str(ROOT / "make_samples.py")], check=True)


def lint(kind):
    base = ROOT / kind
    return run_checks(base / "Sales.Report", base / "Sales.SemanticModel", FAMILY)


def test_healthy_is_clean():
    assert lint("healthy") == []


def test_broken_trips_every_check():
    counts = Counter(f.check for f in lint("broken"))
    assert counts == {"filters": 2, "family": 1, "refs": 1, "geometry": 2, "tmdl": 1}


def test_filter_names_the_culprit_measure():
    msgs = [f.why for f in lint("broken") if f.check == "filters"]
    assert any("Deals at stage" in m for m in msgs)
    assert any("hand-edited" in m for m in msgs)


def test_without_model_filters_report_more_and_refs_do_not_run():
    base = ROOT / "broken"
    findings = run_checks(base / "Sales.Report", None, FAMILY)
    assert not any(f.check in {"refs", "tmdl"} for f in findings)


def test_cli_exit_codes():
    assert main(["lint", str(ROOT / "healthy"), "--layers", "checks", "--family", FAMILY]) == 0
    assert main(["lint", str(ROOT / "broken"), "--layers", "checks", "--family", FAMILY]) == 1


def test_safe_copy_creates_a_new_item(tmp_path):
    import json
    dest = safe_copy(ROOT / "healthy", "Sales", "ZZ-Sales-test", tmp_path / "copy")
    platform = json.loads((dest / "ZZ-Sales-test.Report" / ".platform").read_text())
    assert platform["metadata"]["displayName"] == "ZZ-Sales-test"
    assert platform["config"]["logicalId"] != "7f1c2b9e-0d4a-4a55-9a51-5e3c1d2b0a02"
    pbir = json.loads((dest / "ZZ-Sales-test.Report" / "definition.pbir").read_text())
    assert pbir["datasetReference"]["byPath"]["path"] == "../ZZ-Sales-test.SemanticModel"
    assert (dest / "ZZ-Sales-test.SemanticModel" / "definition" / "tables" / "Deals.tmdl").exists()
