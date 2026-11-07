# Code style

How code and prose in this repo are written so someone skimming a diff
reads it at speed. Rules only; each why is one line. Adapted from nixos-configs'
`wiki/conventions.md` and `wiki/module-style-guide.md` — ported at the
mechanism level, nouns rewritten (skill `port-agent-tooling`). The rules
general enough to live inline are also in [AGENTS.md](../AGENTS.md); this
page is the full guide, and both get edited in the same change.

## Names

- **A function's name is its behavior; a variable's name is its content.**
  Someone skimming should never need to open a function to know what a line
  does: `duration_seconds`, not `d`; `flag_results`, not `caps`.
- **No one-letter names in code you write**, unless the letter is the idiom
  of the code right beside it (`rc` for a return code, `i, rec` in this
  repo's existing loops). Match the neighbors before you improve on them —
  a rename of existing code is its own change, not a rider.
- **A name that needs a comment to be decoded gets a better name instead.**
  The comment may still explain why; it may not be the only thing carrying
  what the name means.

## Comments

- **Rationale sits next to the thing it concerns**, not in a header block:
  the next editor reads a comment in the context it applies to. (This
  repo's module docstrings are the one exception — they state what the
  module is and its trust rules, and the inline comments carry the rest.)
- **A bug recorded in a comment stays in the file** after the fix lands;
  nobody reads `git log`, the comment is what the next editor sees. If a
  later change strands it (the code it described is gone, the name it
  explained has changed), move it to where it still makes sense and write
  it to stand alone — dates, mechanism, outcome — rather than deleting it.
- **When a rename makes the old name ungreppable, say what it was** on the
  declaration: one line containing the old string, so a search for the old
  name lands somewhere that explains it.
- **A scripted rename edits string literals too.** A word-boundary regex
  rename reaches f-string prefixes (`f"..."` becomes the new name), `!r`
  conversions, escape sequences (`"\n"`), short flag strings (`"-d"`) and
  English articles in comments and messages. Grep the rename's diff for
  the new name touching quotes, `!`, backslashes, and dashes, and read
  every changed line that is not code, before trusting the pass.

## Prose

Applies to docs, comments, messages, commit messages, and PR bodies.

- **A list of three or more takes the Oxford comma**: "share it, attach it
  to an issue, or commit it". Without it the last two items can read as one
  pair, and a reader skimming a rule has to stop and parse it.

## Scripts and templates

- **Real logic is a real Python file shipped in `src/`; a template is
  constants and control flow only.** Logic buried in a shell one-liner, a
  TOML string, or a rendered template cannot be unit-tested and drifts from
  the code beside it. This is mappings-are-data from the other side:
  `data/probe-guard-hook.py` is the worked example — a shipped template
  with constants rendered in — not a bash string inside `probe.py`.

## Declined changes get recorded

- **When a "tidier" rewrite was considered and declined, say so where the
  thing lives, with the reason** — so nobody re-litigates it six months
  later. Names work the same way: [docs/vocabulary.md](../docs/vocabulary.md)
  records rejected names with their reasons; a declined upgrade to a
  template, a check, or a CLI shape says so in a comment at the thing
  itself.
