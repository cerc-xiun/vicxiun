# AGENTS.md

## Skills

Skills live in `.agents/skills/`. Load one with the `skill` tool
(`skill({ name: "<name>" })`) when the situation matches. Do not load them
speculatively.

| Load when | Skill |
| --- | --- |
| A plan, design, or idea needs relentless interviewing / stress-testing before any work starts | `grilling` |
| A design conversation should also produce `GLOSSARY.md` and ADRs as it goes | `grill-with-docs` |
| Discussing or editing the project's terminology, `GLOSSARY.md`, or an ADR | `domain-modeling` |
| Unsure which skill or flow fits the situation | `ask-matt` (router — see caveat below) |

`grill-with-docs` is user-invoked only (`disable-model-invocation: true`); it
calls `grilling` and `domain-modeling` internally, both of which are installed.

`ask-matt` is a router over the wider `mattpocock/skills` repo. Only a subset of
its targets is installed here, so it is partially functional by design.

## Agent skills

### Issue tracker

Issues and specs for this repo live as GitHub issues in `cerc-xiun/vicxiun`,
managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles map to identical label strings. See
`docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `GLOSSARY.md` and one `docs/adr/` at the repo root. See
`docs/agents/domain.md`.

## Provenance

All skills come from `mattpocock/skills` and are pinned by content hash in
`skills-lock.json`.

- Update deliberately: `npx skills update`
- Restore from the lockfile: `npx skills experimental_install`
- Add a skill only with its full dependency closure (skills call each other by
  name; a lone wrapper skill has no content of its own).
