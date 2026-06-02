# Nature Workflow Quick Start

`/nature-workflow` is ScholarAIO's bridge to the upstream
[`nature-skills`](https://github.com/Yuan1z0825/nature-skills) bundle. Use it
when the task is explicitly Nature Skills, Nature-style, Nature-family/CNS,
high-impact journal, or Springer Nature oriented.

It is not a simplified local clone. When the original upstream `nature-*` skill
is installed or loadable, use that skill directly. If it is unavailable,
ScholarAIO routes to local fallback skills and states that the output is a
fallback, not an upstream-equivalent Nature Skills result.

## When To Use It

Use `/nature-workflow` for:

- Nature/high-impact scientific figures and publication plots
- Nature-style manuscript polishing or Chinese-to-English academic polish
- Nature-style manuscript section drafting and argument restructuring
- Reviewer-style critique before submission
- Nature/CNS citation support and reference-manager export
- Nature/high-impact Data Availability, repository, source data, code availability, and FAIR checks
- Full-paper bilingual Markdown readers and source-grounded translation
- Reviewer response or rebuttal letters
- Paper-to-PPT / Chinese journal-club decks
- Nature-focused academic search, citation verification, MeSH strategy, and reference management

Submission package is only one scenario. Do not require a submission package
when the user only asks for a figure, polish, reader, paper2ppt deck, citation
search, or Nature Data Availability wording.

## Inputs To Provide

Give the bridge the concrete artifact for the chosen route:

- Figure work: data table, result claim, target backend (`Python` or `R`), and export needs
- Polishing or writing: draft text, section type, target journal/style, and claims that must not change
- Reader or PPT: PDF, DOI, arXiv URL, publisher URL, or existing paper notes
- Citation/search: claim text, scope, preferred citation format, and any journal constraints
- Data Availability: dataset list, access restrictions, repositories, code status, and accession IDs if available
- Response/reviewer work: reviewer comments, decision letter, manuscript draft, and revision notes

## Install Upstream Skills

For direct upstream behavior in Codex, install the full upstream bundle:

```bash
codex plugin marketplace add https://github.com/Yuan1z0825/nature-skills --ref main
codex plugin add nature-skills@nature-skills
```

For manual local-skill installation, clone the upstream repository and copy whole
directories:

```bash
git clone https://github.com/Yuan1z0825/nature-skills.git
cd nature-skills
mkdir -p ~/.codex/skills
cp -R skills/_shared ~/.codex/skills/
for d in skills/nature-*; do
  cp -R "$d" ~/.codex/skills/
done
```

Copying only `SKILL.md` is not enough.

## Common Scenarios

| Scenario | Upstream route | ScholarAIO fallback if upstream is unavailable |
|----------|----------------|------------------------------------------------|
| Nature-style figure | `nature-figure` | `/draw` |
| Manuscript polish | `nature-polishing` | `/writing-polish` |
| Section drafting | `nature-writing` | `/paper-writing` |
| Mock reviewer critique | `nature-reviewer` | bounded fallback critique, then `/paper-writing` or `/citation-check` for fixes |
| Citation support | `nature-citation` | `/search` + `/citation-check` + `/export` |
| Data Availability | `nature-data` | gap inventory, then `/paper-writing` or `/writing-polish` wording |
| Full-paper reader | `nature-reader` | `/show` + `/translate` |
| Reviewer response | `nature-response` | `/review-response` |
| Paper-to-PPT | `nature-paper2ppt` | `/show` or `/paper-guided-reading`, then `/document` |
| Academic search | `nature-academic-search` | `/search`, `/websearch`, `/citation-check`, `/export` |

## Example Prompts

```text
/nature-workflow Polish this abstract in Nature Communications style.
```

```text
/nature-workflow Use Python to create a Nature-style multi-panel figure from these benchmark results.
```

```text
/nature-workflow Turn this PDF into a Chinese journal-club PPT.
```

```text
/nature-workflow Draft a Data Availability statement and repository plan for this manuscript.
```

```text
/nature-workflow Simulate a Nature-style reviewer critique for this draft.
```

## Expected Outputs

The first response should identify:

1. `Upstream target`: selected `nature-*` skill or skill sequence.
2. `Mode`: direct upstream use or ScholarAIO fallback.
3. `Route`: exact route and fallback skills if needed.
4. `Immediate next action`: file to inspect, command to run, or one blocking question.
5. `Guardrails`: source grounding, no invented citations, no invented data identifiers, and required verification.

## Production Demo

The tracked demo fixture lives at
`tests/fixtures/nature_workflow_demo/`. Run its verifier with:

```bash
python tests/fixtures/nature_workflow_demo/verify_demo.py
```

The fixture also includes an executable route demo. It classifies practical
prompts, writes route cards, and proves both direct-upstream and fallback modes:

```bash
python tests/fixtures/nature_workflow_demo/run_demo.py --output route-cards.md
```

For a research-output-level demo, generate the full artifact package:

```bash
python tests/fixtures/nature_workflow_demo/run_product_demo.py --output-dir workspace/_system/issue-107-routing-eval/nature-workflow-product-demo
```

That package includes `route-cards.md`, `inputs/source-data.csv`, SVG/PDF/PNG
figure exports, a polished abstract, a Data Availability statement, a generated
PPTX deck, and QA reports.

The verifier checks that the bridge covers the full upstream 10-skill index,
records the direct-use policy, executes the route demo, and does not collapse
the workflow into a submission-only package.

## Guardrails

- Use the original upstream `nature-*` skill directly when available.
- Do not emulate upstream behavior from a short local summary.
- Do not copy only `SKILL.md`; use the whole skill directory and `skills/_shared` when needed.
- Mark ScholarAIO routes as fallbacks when upstream is unavailable.
- Do not invent citations, reviewer expectations, journal rules, data IDs, repository URLs, source data, figures, or manuscript claims.
