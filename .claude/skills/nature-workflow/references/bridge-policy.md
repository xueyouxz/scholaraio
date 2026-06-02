# Bridge Policy

This bridge integrates ScholarAIO with the upstream `nature-skills` project
without degrading that project into a simplified local rewrite.

## Direct Use Comes First

Use the original upstream nature-* skill directly whenever it is installed or
available through the current agent host. The upstream skill is the source of
truth for its workflow.

Do not emulate upstream behavior from this bridge. Do not copy only SKILL.md.
Do not flatten upstream `static/`, `references/`, scripts, assets, or
`manifest.yaml` into a short local summary.

Direct upstream use means the whole skill directory is available, including:

- `SKILL.md`
- `manifest.yaml`, when present
- `static/`
- `references/`
- scripts and assets
- `skills/_shared` for upstream skills that reference the shared layer

## What This Bridge Does

- Classifies the user's request into the upstream `nature-*` skill index.
- Preserves the upstream product language: Nature/high-impact academic workflow,
  not submission-only.
- Routes to the original upstream skill when possible.
- Routes to ScholarAIO's existing skills only as a fallback or for local
  repository-native work.
- States clearly when a result is a ScholarAIO fallback rather than an
  upstream-equivalent result.

## What This Bridge Does Not Do

- It does not vendor or rewrite the entire upstream `nature-skills` repository.
- It does not claim that `/draw`, `/writing-polish`, `/paper-writing`,
  `/document`, or other ScholarAIO fallbacks are identical to upstream
  `nature-*` workflows.
- It does not require a submission package unless the user explicitly asks for
  one.
- It does not invent citations, data identifiers, repository URLs, reviewer
  expectations, journal rules, figure evidence, manuscript claims, or policy
  details.

## Upstream Installation Reminder

The upstream repository explicitly warns that each skill is a folder-based unit.
Install or reference the whole skill directory, plus `skills/_shared` when
needed. Copying only `SKILL.md` silently breaks many upstream workflows.

Upstream repository checked during this branch:

```text
https://github.com/Yuan1z0825/nature-skills.git
local checked HEAD: 5e31dbb235fc9aca8d40dadd160583d2403dc9f7
origin/main observed: 5e31dbb235fc9aca8d40dadd160583d2403dc9f7
```

If exact parity matters, refresh the upstream clone and inspect the current
`README.md`, `SKILL.md`, `manifest.yaml`, `static/`, and `references/` files
before finalizing behavior.
