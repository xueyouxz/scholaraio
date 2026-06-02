# Route Matrix

Submission package is only one scenario. The upstream `nature-skills` bundle is
a broad Nature/high-impact academic workflow collection.

| Scenario | Upstream target | ScholarAIO fallback |
|----------|-----------------|---------------------|
| Nature/high-impact figure or publication plot | `nature-figure` | `/draw` |
| Manuscript polishing or Chinese-to-English academic polish | `nature-polishing` | `/writing-polish` |
| Abstract, introduction, method, results, discussion, conclusion, title, or manuscript argument drafting | `nature-writing` | `/paper-writing` |
| Reviewer-style critique or pre-submission review | `nature-reviewer` | bounded fallback critique, then `/paper-writing` or `/citation-check` for fixes |
| Nature/CNS citation support and reference-manager export | `nature-citation` | `/search` + `/citation-check` + `/export` |
| Data Availability, source data, code availability, repository, FAIR metadata | `nature-data` | fallback inventory, then wording via `/paper-writing` or `/writing-polish` |
| Full-paper bilingual reader or source-grounded Markdown reader | `nature-reader` | `/show` + `/translate` |
| Reviewer response, rebuttal, major/minor revision letter | `nature-response` | `/review-response` |
| Paper to Chinese journal-club PPT or academic presentation deck | `nature-paper2ppt` | `/show` or `/paper-guided-reading`, then `/document` |
| Academic search, citation verification, MeSH strategy, reference management | `nature-academic-search` | `/search`, `/websearch`, `/citation-check`, `/export` |

The bridge should not require a submission package unless the user explicitly
asks for one.
