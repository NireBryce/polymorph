---
name: promote-to-main
description: How to bring experimental to main so the usage reference, the man page, and the docs match what the program does.
when_to_use: Before opening or merging the PR from experimental to main, or when asked to stabilize, release, or bump main.
---

# Promoting experimental to main

## Applies to

The PR `experimental` → `main` (skill `ship`, "The experimental tier"). Not for the
per-task PRs onto `experimental`; those only keep `just preflight` green.

## Why

`docs/usage.md` and `man/polymorph.1` are written from the real argparse parser by
`scripts/gen_reference.py`, so a flag cannot exist without being in them. They can still be
stale in the repo until someone regenerates them, and the hand-written prose around them
(`docs/cli.md`, README, SPEC) can disagree with the program. `main` is what users read.

## Do

1. Start from a current `experimental` in your own worktree (skill `use-a-worktree`).
2. `just reference`, then `git diff -- docs/usage.md man/polymorph.1`. A diff means the
   parser moved since the last regeneration; commit it.
3. `just preflight`. `docs` fails if either generated file is out of date, if a subcommand
   is missing from `docs/cli.md`, or if a link or the experimental banner is broken.
4. Run the program, not only the tests, for what changed since `main`: for each new or
   changed subcommand in `git log main..experimental`, run it on `tests/fixture_repo` (or a
   scratch copy) and compare its output with the section in `docs/cli.md`. Fix the doc, or
   the program, and say which.
5. Read `docs/cli.md`, `docs/SPEC.md`, and the README commands list against
   `docs/usage.md`: every flag in the usage reference is explained, no flag is described
   that the usage reference does not have (skill `docs-sync`).
6. `CHANGELOG.md`: every user-visible change since `main` has a line; breaking changes say
   what breaks.
7. Issues: each per-task PR closed its own at `ship-land` (skill `ship`, step 3), so
   this PR's body needs no `Closes` lines. An issue still open that a promoted PR
   resolved is closed by hand, and named in the PR body.
8. The README notice ("Experimental and unvetted") stays. Do not describe the project as
   stable, safe, or production-ready in the PR body either.
9. The PR body's `## Risk and impact` (`wiki/risk-and-impact.md`) rates the promotion as a
   whole: the riskiest promoted change sets the risk, and the blurb names it.
10. Say which rung you reached (unit tests, ran the CLI, ran in a live harness) for each
   claim in the PR body. Merge with `gh pr merge` after `just ci-wait`; one confirmation
   covers the merge.

## Do not

- Edit `docs/usage.md` or `man/polymorph.1` by hand: `just reference` overwrites them. To
  change a description, change the `help=` text in `cli.py` (or the constants at the top of
  `scripts/gen_reference.py` for the man page's prose sections).
- Claim the man page renders: the check compares text against the parser; render it with
  `man -l man/polymorph.1` yourself if you want to see it.
