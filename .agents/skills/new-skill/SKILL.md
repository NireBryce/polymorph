---
name: new-skill
description: How to write a new SKILL.md in this repo, or fix an existing skill's frontmatter.
when_to_use: Creating a skill under .agents/skills, or editing a skill's description or when_to_use.
---

# Writing a skill

## Applies to

Creating `.agents/skills/<name>/SKILL.md` or editing a skill's `description`
or `when_to_use`. `tests/docs.py` checks the frontmatter and that
`AGENTS.md` lists the skill.

## The rule

| Where | Holds |
|---|---|
| `description` | One sentence: what the skill does. No paths, no triggers. |
| `when_to_use` | Short trigger phrases, one line. |
| `## Applies to` (right after the H1) | The full trigger list, non-triggers and exceptions. Nothing lives only in `when_to_use`: other harnesses ignore it. |

- Only the keys `name`, `description`, `when_to_use`; a misspelt key is
  silently ignored. `name` equals the directory name.
- Plain YAML scalars: never open with a quote, no `: ` or ` #` inside.
- Body: `## Applies to`, then the procedure. Use real paths and commands
  from this repo; point to the file that owns a fact instead of restating
  it (skill `docs-sync`).
- Add a row to `AGENTS.md`'s skill table, then run `just docs`.
