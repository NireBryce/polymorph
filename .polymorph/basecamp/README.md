# Basecamp store

This directory is the basecamp store (see docs/vocabulary.md): the one managed
place where canonical copies of the repo's core agent files live. The paths
agents expect — `AGENTS.md` at the root, `.agents/skills/` — are symlinks into
it. Edit here or through a link; it is the same file.

Harness config (a harness's `settings.json`, hook scripts under `.agents/hooks/`)
is not store content: it stays where the harness reads it. The directory is named
for the basecamp concept, not for polymorph — polymorph is one basecamp manager
among potential others.
