# Risk and impact ratings

Every PR body, and every issue that proposes a change, ends with two
ratings out of 10 and a one-line reason for each, so a reader can triage
without reading the diff. The PR template, skill `ship` and skill
`propose-issue` point here; this page owns the scale.

```
## Risk and impact

- **Risk: N/10** -- what could break, and how a break would show.
- **Impact: N/10** -- who gains what, and how much.
```

The section goes last, before the generated-by footer. Skip it where
nothing changes: a harness report, a question, a promotion PR made only
of PRs already rated (say so in one line instead).

## Risk: what could go wrong if this change is wrong

Rate the change as written, with its tests; say what the number rests on.

| Score | Means | Examples here |
|---|---|---|
| 1-2 | Text only; a mistake misleads a reader, nothing runs differently | docs, skills, wiki, comments |
| 3-4 | Tooling or tests change; users' runs are unchanged | `scripts/`, `justfile`, a new test, CI |
| 5-6 | Behaviour users see changes, and tests cover it | a CLI flag or output, `inventory`, `equip`, `discover` |
| 7-8 | The safety path or a breaking change: a hook could run differently | `adapter.py`, `generate.py`, `check.py`, `validate.py`, a mapping, the schema |
| 9-10 | A wrong result fails open or loses data, and nothing live has checked it | a deny route, a `verified` mapping, trust rules, deleting or moving users' files |

Raise one step when the change was seen only in unit tests and touches a
harness's behaviour; lower one step when it ran in a live harness.

## Impact: what this change is worth if it is right

| Score | Means | Examples here |
|---|---|---|
| 1-2 | Cosmetic; nobody would notice its absence | wording, a rename inside one module |
| 3-4 | Less friction for maintainers or agents working here | a stale doc fixed, a clearer skill, a script fallback |
| 5-6 | Fixes a real bug or unblocks a workflow someone hit | a wrong finding, a step that had to be done by hand |
| 7-8 | A capability users will use, or wrong output made right | a new subcommand, a mapping fix that changes generated configs |
| 9-10 | Closes a fail-open or makes a harness usable at all | a deny that did not block now blocks; a first mapping verified live |

## Writing the blurb

One line each, plain prose. Risk names the failure, not the feeling
("a recipe the runner parses differently from just would run the wrong
command; the real justfile is parsed in the tests"), not "low risk".
Impact names who gains ("agents in containers without just can run
preflight"). A rating is a judgement, so a reviewer may disagree with
it in the PR; change the number then, not the scale.
