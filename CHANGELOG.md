# Changelog

polymorph is experimental. Anything here can change without notice; this file says what
changed and, from the first release that has users, how it breaks them.

## 0.0.1.dev0 (unreleased)

First state of the project. Python 3.11+, standard library only.

- **Reader:** reads `.agents/settings.json` and hook scripts under `.agents/hooks/` into a
  canonical config (three events, five tool classes). Settings with no cross-harness
  meaning are reported as unmapped, not dropped. Hook commands must be bare script paths
  that resolve under `.agents/hooks/`.
- **Mappings:** TOML, `schema = 1`, with `delivery = "direct"` or `"adapter"`. Shipped:
  `claude` and `zcode` (with `verified` text stating their basis) and a `gemini` draft with
  no `verified`.
- **`generate`:** writes JSON hook configs using the `claude-settings` and `claude-like`
  templates, with `--check` for drift. Lookup of mappings from the repo's
  `.agents/polymorph/mappings/` overrides the built-in ones with a warning.
- **`check`:** static analysis with stable finding codes; text, JSON and SARIF 2.1.0 output;
  `--strict`.
- **`adapter`:** runtime shim translating native payloads to the canonical protocol and
  answers back, exporting `GUARD_*` capability variables; faults produce a visible
  "NOT checked" message and never a deny.
- **`validate` and `conformance`:** mapping validation and fixture replay, with fixtures for
  `claude` and `zcode`.
- **`discover`:** `init`, `log`, `summary`, `draft`, `reload-check`. Logs are redacted by
  default; `--full` and `--env-be-very-careful` are opt-ins. Drafts are unverified and
  built only from observed names.
- **Report path:** a `report-harness` agent skill and a Harness report issue form.
- **Docs:** getting started, concepts, writing a mapping, CLI reference, discovery, CI and
  limits.
- Packaging: a Nix flake and `pyproject.toml`. Mappings and fixtures are found beside the
  source tree, so install editable (`pip install -e .`) or use the Nix package.
