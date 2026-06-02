# Upstream Skill Map

This map follows the upstream `nature-skills` README skill index. It is a route
map, not a replacement for the upstream skills.

| Upstream skill | Upstream purpose | Typical trigger | ScholarAIO fallback when upstream is unavailable |
|----------------|------------------|-----------------|--------------------------------------------------|
| `nature-figure` | Nature/high-impact Python or R figure workflow with figure contract, export QA, and source-data traceability. | Nature figure, publication plot, scientific figure, figures4papers, manuscript figure. | `/draw` for diagrams/visuals; keep evidence/source-data checks explicit. |
| `nature-polishing` | Nature-style academic prose polishing, restructuring, and Chinese-to-English manuscript refinement. | Nature style, polish, academic writing, manuscript paragraph, abstract polish. | `/writing-polish`. |
| `nature-writing` | Nature-style manuscript section drafting and argument restructuring. | Nature writing, write abstract, write introduction, manuscript draft, section reconstruction. | `/paper-writing`. |
| `nature-reviewer` | Nature-style reviewer assessment with three referee reports and cross-review synthesis. | Nature reviewer, pre-submission review, mock peer review, reviewer report, critique. | Use `/paper-writing` or `/citation-check` only for downstream fixes; if no upstream skill exists, produce a bounded fallback critique and mark it non-equivalent. |
| `nature-citation` | Strict Nature/CNS-family citation retrieval, claim segmentation, support grading, and ENW/RIS/Zotero RDF export. | Nature citation, CNS citation, supporting references, add citations, reference export. | `/search` + `/citation-check` + `/export`. |
| `nature-data` | Nature/Springer Nature Data Availability statements, repository plans, dataset citations, FAIR metadata checks. | Data Availability, repository, FAIR metadata, source data, accession number, DOI, code availability. | Use this bridge for gap inventory, then `/paper-writing` or `/writing-polish` only for wording; mark as fallback. |
| `nature-reader` | Full-paper bilingual Markdown reader with source anchors, figure/table grounding, and no summary-only degradation. | Nature reader, full markdown, paper md, 原文对照, 图文对应, 全文翻译. | `/show` + `/translate`. |
| `nature-response` | Point-by-point reviewer response letters with comment triage, action mapping, and risk checks. | response to reviewers, rebuttal letter, major revision, 审稿意见回复. | `/review-response`. |
| `nature-paper2ppt` | Chinese PPTX decks from scientific papers for journal club, group meeting, or academic presentation. | paper PPT, journal club, paper to slides, paper presentation, 组会PPT. | `/show` or `/paper-guided-reading` for evidence, then `/document`. |
| `nature-academic-search` | Multi-source literature search, citation verification, MeSH strategy, citation-file management, and reference management. | search papers, academic search, literature search, verify DOI, reference management. | `/search`, `/websearch`, `/citation-check`, `/export`. |

## Route Principles

- Prefer the upstream skill when available.
- Use ScholarAIO fallbacks only when the upstream skill cannot be executed in
  the current environment.
- Keep the upstream trigger breadth: this is not submission-only.
- Submission package is only one scenario; most upstream skills can be invoked
  for ordinary high-impact academic writing, reading, figures, slides, citation,
  search, or data work.
