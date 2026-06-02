# Upstream Direct-Use Policy

Use the original upstream nature-* skill directly when the current agent host
has it installed or can load it from the upstream repository.

This demo is not a simplified `nature-skills` clone. It documents a bridge.

Do not emulate upstream behavior from a short local summary. Do not copy only skill.md.
Use the whole skill directory, including `manifest.yaml`, `static/`,
`references/`, scripts, assets, and `skills/_shared` when the upstream skill
references shared material.

When the original upstream skill is unavailable, ScholarAIO may use fallback
routes, but the response must say that it is a fallback and must not claim
upstream-equivalent output.
