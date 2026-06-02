from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent

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


def _read(name: str) -> str:
    path = ROOT / name
    if not path.exists():
        raise AssertionError(f"missing required file: {name}")
    return path.read_text(encoding="utf-8")


def _assert_upstream_skill_coverage() -> None:
    combined = "\n".join(
        [
            _read("00-artifact-manifest.md"),
            _read("02-route-matrix.md"),
        ]
    )
    missing = sorted(skill for skill in UPSTREAM_SKILLS if skill not in combined)
    if missing:
        raise AssertionError(f"missing upstream skills: {', '.join(missing)}")


def _assert_direct_use_policy() -> None:
    policy = _read("01-upstream-policy.md").lower()
    for phrase in (
        "use the original upstream nature-* skill directly",
        "do not emulate",
        "do not copy only skill.md",
        "whole skill directory",
        "skills/_shared",
    ):
        if phrase not in policy:
            raise AssertionError(f"missing direct-use policy phrase: {phrase}")


def _assert_not_submission_only() -> None:
    route_matrix = _read("02-route-matrix.md").lower()
    for phrase in (
        "submission package is only one scenario",
        "reader",
        "paper2ppt",
        "figure",
        "polishing",
        "academic search",
    ):
        if phrase not in route_matrix:
            raise AssertionError(f"missing breadth phrase: {phrase}")


def _assert_executable_route_demo() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "route-cards.md"
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "run_demo.py"),
                "--output",
                str(output_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )

        route_cards = output_path.read_text(encoding="utf-8")
        if "nature-workflow executable route demo: PASS" not in result.stdout:
            raise AssertionError("route demo did not report PASS")
        if "Mode: direct upstream preferred" not in route_cards:
            raise AssertionError("route demo did not include direct upstream mode")
        if "Mode: ScholarAIO fallback" not in route_cards:
            raise AssertionError("route demo did not include fallback mode")
        for skill in UPSTREAM_SKILLS:
            if f"Upstream target: `{skill}`" not in route_cards:
                raise AssertionError(f"route demo missing skill card: {skill}")


def main() -> None:
    _assert_upstream_skill_coverage()
    _assert_direct_use_policy()
    _assert_not_submission_only()
    _assert_executable_route_demo()
    print("nature-workflow bridge demo verifier: PASS")
    print(f"checked upstream skills: {len(UPSTREAM_SKILLS)}")
    print("direct-use policy: PASS")
    print("upstream skill coverage: PASS")
    print("not submission-only: PASS")
    print("executable route demo: PASS")


if __name__ == "__main__":
    main()
