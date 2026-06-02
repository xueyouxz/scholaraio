# Nature Workflow Bridge Demo Manifest

This fixture demonstrates the corrected issue #107 integration posture:
ScholarAIO should bridge to the upstream `nature-skills` bundle rather than
replace it with a simplified local submission package skill.

## Artifacts

| File | Purpose |
|------|---------|
| `00-artifact-manifest.md` | Demo scope and upstream skill coverage. |
| `01-upstream-policy.md` | Direct-use policy: use original upstream `nature-*` skills when available. |
| `02-route-matrix.md` | Full route matrix across the upstream skill index and ScholarAIO fallbacks. |
| `03-demo-prompts.md` | Practical prompts that cover non-submission and submission-related scenarios. |
| `04-verifier-output.md` | Captured verifier output for this tracked fixture. |
| `demo_cases.json` | Executable route-demo cases covering all upstream skills plus fallback mode. |
| `run_demo.py` | Deterministic route demo that classifies prompts and writes route cards. |
| `run_product_demo.py` | Reproducible research-output demo generator for figure, writing, data, PPTX, and QA artifacts. |
| `verify_demo.py` | Local checker for upstream coverage, bridge policy, and executable routing. |

## Upstream Skills Covered

- `nature-figure`
- `nature-polishing`
- `nature-writing`
- `nature-reviewer`
- `nature-citation`
- `nature-data`
- `nature-reader`
- `nature-response`
- `nature-paper2ppt`
- `nature-academic-search`
