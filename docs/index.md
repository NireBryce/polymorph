# polymorph documentation

polymorph is experimental and unvetted; read [Limits](limits.md) before relying on any of it.

| If you want to... | Read |
|---|---|
| Try it on a repo | [Getting started](getting-started.md) |
| Understand the model (canonical config, mappings, delivery, trust) | [Concepts](concepts.md) |
| Add support for a harness whose behaviour you know | [Writing a mapping](writing-a-mapping.md) |
| Add support for a harness whose behaviour you do not know | [Discovery](discovery.md), or `polymorph capture` ([CLI](cli.md#capture); runs a model, read [Cost and responsibility](../README.md#cost-and-responsibility)) |
| Look up a command or flag | [CLI reference](cli.md) |
| Run it in pre-commit or GitHub Actions | [CI](ci.md) |
| Know what is unverified and what "verified" means | [Limits](limits.md) |
| Read the contract the code follows | [SPEC](SPEC.md) |
| See what a real harness sent | [Observed: t3code](observed-t3code.md), [Observed: OpenCode](observed-opencode.md) (with an [example plugin](examples/opencode-discovery-plugin.js)), [Observed: a live ZCode session](observed-zcode-session.md) |
| Use the pivot's words correctly | [Vocabulary](vocabulary.md) |
| See where the scope goes next | [Loadout pivot plan](plan-loadout-pivot.md) |
| See the phases after the agent log | [Plan: the phases after the agent log](plan-next-phases.md) |
| Contribute or report a harness | [CONTRIBUTING](../CONTRIBUTING.md) |
| See what changed | [CHANGELOG](../CHANGELOG.md) |

[SPEC](SPEC.md) is the contract; these pages explain it and describe what the code does.
Where they disagree, the code is the tiebreaker and the disagreement is a bug to report.
