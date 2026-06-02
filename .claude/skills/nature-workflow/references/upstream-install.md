# Upstream Install

Use this reference when the user wants direct upstream Nature Skills behavior,
asks whether ScholarAIO has copied a simplified version, or needs to make the
original `nature-*` skills available in the current agent host.

## Codex Plugin Path

Install the complete upstream bundle through the Codex plugin marketplace:

```bash
codex plugin marketplace add https://github.com/Yuan1z0825/nature-skills --ref main
codex plugin add nature-skills@nature-skills
```

Restart Codex or start a new session after installation so the `nature-*` skills
are discovered.

## Manual Local-Skill Path

Clone the upstream repository:

```bash
git clone https://github.com/Yuan1z0825/nature-skills.git
cd nature-skills
```

Install all current upstream skills as whole directories:

```bash
mkdir -p ~/.codex/skills
cp -R skills/_shared ~/.codex/skills/
for d in skills/nature-*; do
  cp -R "$d" ~/.codex/skills/
done
```

Install one upstream skill as a whole directory:

```bash
mkdir -p ~/.codex/skills
cp -R skills/_shared ~/.codex/skills/
cp -R skills/nature-reader ~/.codex/skills/
```

## Fidelity Rule

Copy the whole skill directory. Do not copy only `SKILL.md`.

The original upstream skills may rely on `manifest.yaml`, `static/`,
`references/`, scripts, assets, README context, and `skills/_shared`. A local
summary or partial folder is not the same product and must be described as a
fallback or adaptation.
