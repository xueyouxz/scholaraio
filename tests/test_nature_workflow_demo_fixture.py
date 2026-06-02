"""Checks for the versioned nature-workflow bridge demo fixture."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "nature_workflow_demo"

UPSTREAM_SKILLS = {
    "nature-figure",
    "nature-polishing",
    "nature-writing",
    "nature-reviewer",
    "nature-citation",
    "nature-data",
    "nature-reader",
    "nature-response",
    "nature-paper2ppt",
    "nature-academic-search",
}


def test_tracked_demo_fixture_runs_its_verifier() -> None:
    result = subprocess.run(
        [sys.executable, str(FIXTURE_DIR / "verify_demo.py")],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "nature-workflow bridge demo verifier: PASS" in result.stdout
    assert "upstream skill coverage: PASS" in result.stdout
    assert "executable route demo: PASS" in result.stdout


def test_tracked_demo_fixture_runs_executable_route_demo(tmp_path: Path) -> None:
    output_path = tmp_path / "route-cards.md"

    result = subprocess.run(
        [
            sys.executable,
            str(FIXTURE_DIR / "run_demo.py"),
            "--output",
            str(output_path),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    route_cards = output_path.read_text(encoding="utf-8")

    assert "nature-workflow executable route demo: PASS" in result.stdout
    assert "# Nature Workflow Executable Route Demo" in route_cards
    assert "Mode: direct upstream preferred" in route_cards
    assert "Mode: ScholarAIO fallback" in route_cards
    assert "Immediate next action:" in route_cards
    assert "Guardrails:" in route_cards

    fallback_card = route_cards.split("## figure-fallback", maxsplit=1)[1]
    fallback_action = fallback_card.split("Guardrails:", maxsplit=1)[0]
    assert "Immediate next action: Use ScholarAIO fallback path: /draw" in fallback_action
    assert "Load upstream" not in fallback_action

    for skill_name in UPSTREAM_SKILLS:
        assert f"Upstream target: `{skill_name}`" in route_cards


def test_documented_demo_prompts_are_executable_route_cases(tmp_path: Path) -> None:
    cases_path = tmp_path / "documented-demo-cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "name": "documented-polishing",
                        "prompt": "/nature-workflow Polish this abstract in Nature style.",
                        "expected_upstream": "nature-polishing",
                        "upstream_available": True,
                    },
                    {
                        "name": "documented-paper2ppt",
                        "prompt": "/nature-workflow Turn this paper into a Chinese journal-club PPT.",
                        "expected_upstream": "nature-paper2ppt",
                        "upstream_available": True,
                    },
                    {
                        "name": "documented-figure",
                        "prompt": "/nature-workflow Make a Nature-style figure in Python from these benchmark results.",
                        "expected_upstream": "nature-figure",
                        "upstream_available": True,
                    },
                    {
                        "name": "documented-reader",
                        "prompt": "/nature-workflow Build a source-grounded bilingual Markdown reader for this PDF.",
                        "expected_upstream": "nature-reader",
                        "upstream_available": True,
                    },
                    {
                        "name": "documented-data",
                        "prompt": "/nature-workflow Draft a Data Availability statement and repository plan.",
                        "expected_upstream": "nature-data",
                        "upstream_available": True,
                    },
                    {
                        "name": "documented-reviewer",
                        "prompt": "/nature-workflow Simulate a Nature-style reviewer critique before submission.",
                        "expected_upstream": "nature-reviewer",
                        "upstream_available": True,
                    },
                    {
                        "name": "documented-response",
                        "prompt": "/nature-workflow Draft a point-by-point response to these reviewer comments.",
                        "expected_upstream": "nature-response",
                        "upstream_available": True,
                    },
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            str(FIXTURE_DIR / "run_demo.py"),
            "--cases",
            str(cases_path),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "nature-workflow executable route demo: PASS" in result.stdout
    assert "checked route cases: 7" in result.stdout


def test_tracked_product_demo_generates_research_artifacts(tmp_path: Path) -> None:
    pytest.importorskip("matplotlib")
    pytest.importorskip("numpy")
    pytest.importorskip("pptx")

    output_dir = tmp_path / "product-demo"

    result = subprocess.run(
        [
            sys.executable,
            str(FIXTURE_DIR / "run_product_demo.py"),
            "--output-dir",
            str(output_dir),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "nature-workflow product demo: PASS" in result.stdout

    expected_files = (
        "README.md",
        "inputs/source-data.csv",
        "route-cards.md",
        "figures/nature-workflow-product-demo.svg",
        "figures/nature-workflow-product-demo.pdf",
        "figures/nature-workflow-product-demo.png",
        "figures/figure-caption.md",
        "figures/figure-qa.md",
        "writing/abstract-before.md",
        "writing/abstract-polished.md",
        "data/data-availability.md",
        "slides/nature-workflow-product-demo.pptx",
        "slides/pptx-inspect.md",
        "qa/product-demo-verification.md",
    )
    for rel_path in expected_files:
        assert (output_dir / rel_path).exists(), rel_path

    manifest = (output_dir / "README.md").read_text(encoding="utf-8")
    qa = (output_dir / "qa" / "product-demo-verification.md").read_text(encoding="utf-8")
    figure_qa = (output_dir / "figures" / "figure-qa.md").read_text(encoding="utf-8")
    polished = (output_dir / "writing" / "abstract-polished.md").read_text(encoding="utf-8")

    for required in (
        "nature-figure",
        "nature-polishing",
        "nature-data",
        "nature-paper2ppt",
        "direct upstream preferred",
        "ScholarAIO fallback",
    ):
        assert required in manifest

    assert "No invented accession IDs" in qa
    assert "source-data.csv" in figure_qa
    assert "This demo uses synthetic data" in polished


def test_product_demo_script_guards_optional_dependencies() -> None:
    script = (FIXTURE_DIR / "run_product_demo.py").read_text(encoding="utf-8")

    assert "importlib.util.find_spec" in script
    assert "nature-workflow product demo requires optional plotting/Office dependencies" in script
    assert "import matplotlib" not in script.split("def _make_figure", maxsplit=1)[0]
    assert "from pptx import" not in script.split("def _write_pptx", maxsplit=1)[0]


def test_tracked_demo_fixture_covers_full_upstream_skill_index() -> None:
    manifest = (FIXTURE_DIR / "00-artifact-manifest.md").read_text(encoding="utf-8")
    route_matrix = (FIXTURE_DIR / "02-route-matrix.md").read_text(encoding="utf-8")
    demo_cases = (FIXTURE_DIR / "demo_cases.json").read_text(encoding="utf-8")

    combined = f"{manifest}\n{route_matrix}\n{demo_cases}"
    for skill_name in UPSTREAM_SKILLS:
        assert skill_name in combined


def test_tracked_demo_fixture_records_direct_use_policy() -> None:
    policy = (FIXTURE_DIR / "01-upstream-policy.md").read_text(encoding="utf-8").lower()

    for required in (
        "use the original upstream nature-* skill directly",
        "do not emulate",
        "do not copy only skill.md",
        "whole skill directory",
        "skills/_shared",
    ):
        assert required in policy


def test_tracked_demo_fixture_is_not_submission_package_only() -> None:
    route_matrix = (FIXTURE_DIR / "02-route-matrix.md").read_text(encoding="utf-8").lower()

    assert "submission package is only one scenario" in route_matrix
    for scenario in ("reader", "paper2ppt", "figure", "polishing", "academic search"):
        assert scenario in route_matrix
