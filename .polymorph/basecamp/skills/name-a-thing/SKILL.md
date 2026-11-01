---
name: name-a-thing
description: How to name a new concept, command or file in this repo so the name says what it means.
when_to_use: Naming a feature, subcommand, document or directory here, or when a name starts feeling wrong.
---

# Naming a thing

## Applies to

Choosing a name for anything this repo will keep: a subcommand, a document, a
directory, a concept in the docs. Not for variable names in code (follow the
surrounding style).

## The rule

Test a candidate against these, in order:

1. **Says what it means.** A reader who has never met the project gets it
   right on first reading. Role nouns and borrowed jargon that need a
   footnote (quartermaster, loadmaster) fail this.
2. **Survives translation.** The dictionary sense, not the in-community
   sense, is what a translator lands on (spawn fails this).
3. **Collides with nothing.** Check the GitHub vocabulary around it, the CLI
   surface, and the retired words in `docs/vocabulary.md`. If a collision is
   unavoidable, the style guide states the disambiguation rule.
4. **One word, one job.** Each word in the system names one thing; two words
   for one thing is a bug (kit and loadout, issue and equip).

Decisions go in `docs/vocabulary.md`: the winner under "The words" with its
one-job definition, the losers under "Retired words" with the reason they
were rejected. A retired word without a recorded reason gets re-proposed.

Plain register: real words doing their ordinary job, no military or gaming
flavour on a safety-path tool.
