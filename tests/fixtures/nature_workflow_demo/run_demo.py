from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_CASES = ROOT / "demo_cases.json"


@dataclass(frozen=True)
class Route:
    upstream: str
    keywords: tuple[str, ...]
    fallback: str
    next_action: str
    guardrails: tuple[str, ...]


ROUTES = (
    Route(
        upstream="nature-figure",
        keywords=(
            "nature figure",
            "nature style figure",
            "multi panel figure",
            "publication plot",
            "figure in python",
            "python figure",
            "figure from",
            "figures4papers",
        ),
        fallback="/draw",
        next_action="Load upstream skills/nature-figure/SKILL.md, then resolve the Python/R backend gate.",
        guardrails=("Do not invent source data.", "Keep export and visual QA requirements explicit."),
    ),
    Route(
        upstream="nature-polishing",
        keywords=("polish", "polishing", "nature communications style", "nature style", "prose"),
        fallback="/writing-polish",
        next_action="Load upstream skills/nature-polishing/SKILL.md and detect section, language, and journal axes.",
        guardrails=("Do not change scientific claims.", "Preserve author intent and terminology."),
    ),
    Route(
        upstream="nature-writing",
        keywords=("draft", "write", "introduction", "abstract", "argument", "section"),
        fallback="/paper-writing",
        next_action="Load upstream skills/nature-writing/SKILL.md and map paper type, section, language, and journal.",
        guardrails=("Do not invent results.", "Separate supplied claims from missing evidence."),
    ),
    Route(
        upstream="nature-reviewer",
        keywords=(
            "nature-style reviewer critique",
            "reviewer critique",
            "reviewer assessment",
            "mock peer review",
            "pre-submission review",
        ),
        fallback="bounded fallback critique, then /paper-writing or /citation-check for fixes",
        next_action="Load upstream skills/nature-reviewer/SKILL.md and prepare three referee reports plus synthesis.",
        guardrails=("Do not claim an editorial decision.", "Ground critique in provided manuscript facts."),
    ),
    Route(
        upstream="nature-citation",
        keywords=("nature/cns", "cns-family citation", "citation support", "export ris", "supporting references"),
        fallback="/search + /citation-check + /export",
        next_action="Load upstream skills/nature-citation/SKILL.md and segment claims before searching.",
        guardrails=("Do not invent citations.", "Keep claim-to-reference support explicit."),
    ),
    Route(
        upstream="nature-data",
        keywords=(
            "nature data availability",
            "data availability",
            "repository plan",
            "source data",
            "fair metadata",
            "data sharing",
            "code availability",
            "accession",
        ),
        fallback="gap inventory, then /paper-writing or /writing-polish wording",
        next_action="Load upstream skills/nature-data/SKILL.md and inventory each dataset access route.",
        guardrails=("Do not invent accession IDs.", "State missing repositories or restrictions."),
    ),
    Route(
        upstream="nature-reader",
        keywords=("bilingual markdown reader", "full-paper reader", "source anchors", "paper reader", "full markdown"),
        fallback="/show + /translate",
        next_action="Load upstream skills/nature-reader/SKILL.md and identify source format.",
        guardrails=("Do not degrade into summary-only output.", "Preserve source anchors and figure/table grounding."),
    ),
    Route(
        upstream="nature-response",
        keywords=(
            "point by point",
            "point by point response",
            "reviewer comments",
            "reviewer response",
            "response letter",
            "major revision",
            "rebuttal",
        ),
        fallback="/review-response",
        next_action="Load upstream skills/nature-response/SKILL.md and triage comments before drafting.",
        guardrails=("Do not skip reviewer comments.", "Track revision actions and residual risks."),
    ),
    Route(
        upstream="nature-paper2ppt",
        keywords=("journal-club ppt", "paper to ppt", "paper2ppt", "ppt in nature style", "presentation deck"),
        fallback="/show or /paper-guided-reading, then /document",
        next_action="Load upstream skills/nature-paper2ppt/SKILL.md and classify the paper type.",
        guardrails=("Do not include unsupported slide claims.", "Run overflow and figure-quality checks."),
    ),
    Route(
        upstream="nature-academic-search",
        keywords=("nature-focused academic search", "verify doi", "reference exports", "mesh strategy"),
        fallback="/search + /websearch + /citation-check + /export",
        next_action="Load upstream skills/nature-academic-search/SKILL.md and choose the search workflow.",
        guardrails=("Use source-specific search limits.", "Verify DOI and citation metadata before export."),
    ),
)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value.lower())).strip()


def _load_cases(path: Path) -> list[dict[str, object]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload.get("cases")
    if not isinstance(cases, list):
        raise AssertionError("demo_cases.json must contain a cases list")
    return cases


def _classify(prompt: str) -> Route:
    normalized = f" {_normalize(prompt)} "
    scored: list[tuple[int, int, Route]] = []
    for index, route in enumerate(ROUTES):
        score = sum(1 for keyword in route.keywords if f" {_normalize(keyword)} " in normalized)
        scored.append((score, -index, route))

    score, _neg_index, route = max(scored, key=lambda item: (item[0], item[1]))
    if score <= 0:
        raise AssertionError(f"no route matched prompt: {prompt}")
    return route


def _mode(upstream_available: object) -> str:
    if upstream_available is True:
        return "direct upstream preferred"
    if upstream_available is False:
        return "ScholarAIO fallback"
    raise AssertionError("upstream_available must be true or false")


def _render_card(case: dict[str, object], route: Route) -> str:
    prompt = str(case["prompt"])
    mode = _mode(case["upstream_available"])
    route_line = (
        f"`{route.upstream}` from the whole upstream skill directory"
        if mode == "direct upstream preferred"
        else f"ScholarAIO fallback path: {route.fallback}"
    )
    next_action = (
        route.next_action
        if mode == "direct upstream preferred"
        else f"Use ScholarAIO fallback path: {route.fallback}; state that upstream nature-skills is unavailable."
    )

    guardrails = "\n".join(f"- {item}" for item in route.guardrails)
    return "\n".join(
        [
            f"## {case['name']}",
            "",
            f"Prompt: {prompt}",
            f"Upstream target: `{route.upstream}`",
            f"Mode: {mode}",
            f"Route: {route_line}",
            f"ScholarAIO fallback: {route.fallback}",
            f"Immediate next action: {next_action}",
            "Guardrails:",
            guardrails,
            "",
        ]
    )


def _render_demo(cases: list[dict[str, object]]) -> str:
    cards = ["# Nature Workflow Executable Route Demo", ""]
    for case in cases:
        route = _classify(str(case["prompt"]))
        expected = case["expected_upstream"]
        if route.upstream != expected:
            raise AssertionError(f"{case['name']}: expected {expected}, got {route.upstream}")
        cards.append(_render_card(case, route))
    return "\n".join(cards).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the nature-workflow route demo")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    cases = _load_cases(args.cases)
    rendered = _render_demo(cases)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"wrote route cards: {args.output}")
    else:
        print(rendered, end="")

    print("nature-workflow executable route demo: PASS")
    print(f"checked route cases: {len(cases)}")


if __name__ == "__main__":
    main()
