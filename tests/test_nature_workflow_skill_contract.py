"""Contract tests for the nature-workflow bridge skill."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".claude" / "skills" / "nature-workflow"
SKILL_FILE = SKILL_DIR / "SKILL.md"
REFERENCES_DIR = SKILL_DIR / "references"

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


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_nature_workflow_skill_has_bridge_reference_files() -> None:
    expected = {
        "upstream-skill-map.md",
        "bridge-policy.md",
        "quickstart.md",
        "upstream-install.md",
    }

    assert SKILL_DIR.exists()
    assert {path.name for path in REFERENCES_DIR.glob("*.md")} >= expected


def test_nature_workflow_uses_upstream_skills_directly_when_available() -> None:
    combined = "\n".join(
        [
            _read(SKILL_FILE),
            _read(REFERENCES_DIR / "bridge-policy.md"),
        ]
    )

    for required in (
        "use the original upstream nature-* skill directly",
        "do not emulate",
        "do not copy only skill.md",
        "whole skill directory",
        "skills/_shared",
    ):
        assert required in combined


def test_nature_workflow_documents_executable_upstream_install_paths() -> None:
    text = _read(REFERENCES_DIR / "upstream-install.md")

    for required in (
        "codex plugin marketplace add https://github.com/yuan1z0825/nature-skills --ref main",
        "codex plugin add nature-skills@nature-skills",
        "cp -r skills/_shared",
        "for d in skills/nature-*",
        "copy the whole skill directory",
    ):
        assert required in text


def test_nature_workflow_covers_full_upstream_skill_index() -> None:
    combined = "\n".join(
        [
            _read(SKILL_FILE),
            _read(REFERENCES_DIR / "upstream-skill-map.md"),
        ]
    )

    for skill_name in UPSTREAM_SKILLS:
        assert skill_name in combined


def test_nature_workflow_maps_to_existing_scholaraio_fallbacks() -> None:
    text = _read(REFERENCES_DIR / "upstream-skill-map.md")

    for skill_name in (
        "/draw",
        "/writing-polish",
        "/paper-writing",
        "/review-response",
        "/citation-check",
        "/search",
        "/show",
        "/translate",
        "/document",
    ):
        assert skill_name in text


def test_nature_workflow_is_not_submission_package_only() -> None:
    combined = "\n".join(
        [
            _read(SKILL_FILE),
            _read(REFERENCES_DIR / "quickstart.md"),
        ]
    )

    for scenario in (
        "figure",
        "polishing",
        "writing",
        "reader",
        "paper2ppt",
        "academic search",
    ):
        assert scenario in combined
    assert "submission package is only one scenario" in combined
