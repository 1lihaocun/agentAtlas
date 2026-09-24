# Memory scope rules

**Rules checked: 2026-09-22.** This is a conservative interpretation of retrieved official documentation and source, not a trace of this machine's agents. Upstream `main` URLs are mutable; the checked date is a rule-review date, not a claim that the installed binary matches that revision.

## API and meaning

```python
from atlas.memory_scope import build_memory_view

view = build_memory_view(
    files,                 # catalog metadata, not memory contents
    project_roots=None,     # optional iterable of absolute known project paths
    home=None,              # user HOME directory, not HERMES_HOME / CODEX_HOME
    project='',
    platform='',
    profile='',
    extension_evidence=None,  # optional caller-supplied bounded instruction evidence
)
```

The result has `schemaVersion: 1`, `files`, `projects: [{path, name}]`, `platforms`, `profiles`, `counts: {total, matched, levels}`, `selected: {project, platform, profile}`, and `notice`.

- Only `category == 'memory'` assets enter `files`. Instruction files, skills, references, commands, hooks, configuration, sessions, logs, and other assets are not silently relabeled as memories.
- Each result is a deep copy of the first inventory asset for its exact path, with an added/replaced `semantics` object. Input records and files are never modified. Duplicate paths count once. Conflicting platform/profile metadata for a duplicate produces unknown semantics.
- Existing `scope`, `profile`, and `project` remain **storage/catalog metadata**. They are not evidence of actual runtime loading. In particular, a note about a repository does not acquire repository-only scope.
- `semantics.level` is one of `user`, `profile`, `workspace`, `project`, `directory`, `session`, `unknown`; `label` is Chinese. Directory and session are reserved vocabulary: the currently verified rules do not justify emitting those levels merely because a file is in a nested directory or summarizes one session.
- `appliesTo` lists absolute **known project candidates** under the stated rule and its conditions. It never means “the agent read this while working here.” `allProjects` is true only for the verified shared Codex read store and Hermes profile stores. It is restricted to that asset's platform and profile, not all installed agents.
- `reader` names the documented consumer, separating a foreground conversation from a background consolidation worker. `loading` carries `mode`, Chinese `label`, `trigger`, and `portion`. Modes are `automatic`, `on_demand`, `retrieval`, `manual`, `none`, `unknown`; current rules do not need to emit `manual`.
- `inheritance` describes sharing/containment, not instruction precedence. `basis` contains `kind` (`documented_rule`, `path_inference`, `local_declaration`, `unknown`), `label`, `detail` with the checked date, and `sources: [{title, url}]`. `local_declaration` means a reviewed local declaration supplied by the caller, not an official extension contract or observed read.
- Recognized artifacts additionally expose `role: {id, label}` and `pipeline: {stage, label}`. Stages are `foreground`, `background`, or `unknown`; they identify the supported role, **not an exclusive consumer or runtime observation**. An index, summary, resource, raw input, consolidation note, and extension instructions are distinct roles. General unknown assets may omit these additive schema-v1 fields.
- Accepted local declarations add `basis.localSources: [{path, line, sha256, label}]`. These are plain local-reference metadata, not `file:` URLs, external links, or permission to read arbitrary paths. The supplied declaration text is not copied into the returned semantics.
- Every asset has exactly `observation: {state: 'unverified', label: '未验证实际读取'}`. No instruction usage counts, memory citations, file timestamps, or name matches are promoted into observed-read evidence. `notes` carries caveats.

### Project candidates and filtering

An explicit `project_roots` iterable is authoritative for the option list; `[]` deliberately means no known projects. If omitted, the resolver accepts only an absolute catalog `project` path that contains its asset, excluding hidden home-platform roots. It never parses memory contents, `cwd`, source-session identifiers, frontmatter types, or paths mentioned in prose to discover applicability. Parent callers can provide roots recovered from their already-validated scan grouping metadata.

Relative paths, parent-traversal paths, HOME itself, filesystem roots, and pseudo labels such as `(根目录)` are not project options. Paths are lexical: there is no filesystem traversal, symlink resolution, git lookup, or disk-existence test. The caller must supply its trusted, safety-filtered catalog; this resolver is not a file-access authorization boundary.

Empty selectors mean all. Nonempty platform and profile selectors are exact, conjunctive matches. A project selector matches only `appliesTo`, which already includes known projects for the applicable global/profile-shared rules. Selecting a previously unknown project never adds it to the candidate set. Unknown memories remain visible without a project filter, but are not guessed applicable to a selected project. Options and `counts.total` describe the unfiltered distinct memory inventory; `counts.matched` and `counts.levels` describe the returned rows.

## Verified platform rules

### Hermes: profile-shared snapshots, not repository hierarchy

The built-in `memories/MEMORY.md` and `memories/USER.md` are loaded into a frozen system-prompt snapshot at session start. Writes during a session do not replace its existing frozen snapshot. Corresponding memory/user-profile settings can disable the stores, and external providers have separate behavior.[1]

Profiles have separate Hermes homes and separate memories, with no fallback to another profile's missing memory files. The standard default home is `~/.hermes`; named homes are `~/.hermes/profiles/<profile>`.[2]

Resolver behavior:

- Both default and named stores have `level: profile`, `loading.mode: automatic`, and `allProjects: true`, within that same profile only. “Automatic” remains conditional on that store being enabled and that home being selected.
- The path-derived profile must agree with catalog profile metadata. A default store is not inherited by a named profile, and a named store mislabeled default stays unknown.
- `pending/memory/<id>.json` is an approval proposal, not the active memory snapshot: `level: profile`, `loading.mode: none`, no project applicability. Approval is required before the proposal reaches a real memory store; the proposal contents and approval state are not inspected.[1][13]
- Arbitrary Markdown under `memories/`, custom Hermes homes, and project-local `.hermes/memories/` are not assigned this default-home rule.

### Codex: native read store versus background inputs

The checked read-path source gates native memory context on both the memory feature and `use_memories`. It chooses a memory namespace/version from configuration, reads a nonempty `memory_summary.md`, and injects a bounded summary as developer context. The source budget is 2500 tokens.[7][8][12]

The V1 prompt describes a shared memory directory, a summary already in context, `MEMORY.md` as the searchable registry, and targeted retrieval of relevant rollout summaries or skills. This is not the AGENTS.md ancestor-directory loading mechanism.[9]

Resolver behavior for the standard `~/.codex/memories/` store:

- `memory_summary.md`: `level: user`, conditional `automatic` summary injection.
- `MEMORY.md` and direct `rollout_summaries/*.md`: `level: user`, `retrieval` of relevant portions, not full automatic injection.
- Those read artifacts have cross-project candidate applicability within the same Codex home/profile. A rollout summary's originating session or a repository name in a note does not constrain the shared retrieval store to that origin.
- Only the conventional default profile/home is recognized. Custom homes, alternative memory versions/namespaces, and enabling flags are not read or guessed. Skill-category assets remain outside this memory API.

The write pipeline consolidates selected raw inputs and produces shared output artifacts. `raw_memories.md` and `phase2_workspace_diff.md` are consolidation inputs. The write prompt tells the consolidation worker to read each present extension's `instructions.md` before interpreting that source.[10][11]

Accordingly, those exact background inputs use `loading.mode: on_demand`, explicitly naming the **background consolidation reader**, and have no asserted foreground project applicability. Roles are `raw_memory_input`, `workspace_diff`, and `extension_instructions`. Read-store roles are `memory_summary`, `memory_index`, and `rollout_summary`. A file may have other consumers; a background role here does not establish background-only use.

#### Caller-supplied extension declarations

`build_memory_view` itself still opens no files. Its optional final `extension_evidence` argument is a mapping from the **exact absolute** `~/.codex/memories/extensions/<name>/instructions.md` path (with HOME expanded by the caller) to:

```python
{"content": complete_utf8_text, "sha256": lowercase_sha256_of_utf8_bytes, "truncated": False}
```

The trusted caller must obtain this through its bounded, safety-filtered, same-platform catalog reader. Evidence is a separate API argument: an asset's metadata cannot self-declare it. The resolver requires literal `truncated: False`, verifies the UTF-8 digest, and matches a reviewed **full-text snapshot** for the same extension. This deliberately rejects even otherwise harmless text/whitespace edits until re-reviewed; keyword matching could accept appended contradictory instructions. Hashes identify reviewed text, not its authenticity, current existence, or actual runtime consumption. Missing, malformed, truncated, changed, incorrectly hashed, differently located, and unknown-extension declarations fail closed. Evidence exceeding 65,536 characters is rejected before encoding. No resource/note content is inspected.

Reviewed local declarations (2026-09-22; paths relative to `~/.codex/memories/extensions/`):

| Instruction reference | Accepted direct child layout | Role | Reviewed SHA-256 |
| --- | --- | --- | --- |
| `skysight/instructions.md:5` (context lines 3–7) | `skysight/resources/*.md` | `extension_resource`: activity summaries | `b0c176f51416e2c2e06e0f582cb42c830fdf37287dc9b0d0dbc5dc37d1f1c556` |
| `chronicle/instructions.md:5` (context lines 3–5) | `chronicle/resources/*.md` | `extension_resource`: work-activity summaries | `f282735a52525411e0faf4fb366c1f1df8707bfc8cb30af06615d1d7c2f1eb35` |
| `ad_hoc/instructions.md:5` (context lines 4–11) | `ad_hoc/notes/*.md` | `consolidation_note`: memory-edit inputs | `d36a36083d92f9d44efbd95e0e4b6e81d7d149e812f2bca2009b6dd4b8aa93e7` |

These are **local declarations**, inspected as data rather than executed instructions. Skysight describes relevant summaries as phase-2 evidence and explicitly treats YAML frontmatter as presentation metadata. Chronicle describes resources as phase-2 input. Ad-hoc describes consolidation of new/edited notes via the workspace diff and warns that note contents are untrusted information, never instructions to perform actions. The official write source establishes the surrounding pipeline, not these local source-specific rules.[10][11]

With matching evidence, those direct Markdown children have `level: user` (the conventional user's Codex memory namespace), `pipeline.stage: background`, `loading.mode: on_demand`, and `basis.kind: local_declaration`. They are **input candidates**, not proof that a worker ran, read them, consolidated them, or included them in any output. `appliesTo` stays empty and `allProjects` false. Resource topics, timestamps, filename slugs, YAML, and repository mentions cannot confer applicability. Runtime enablement, availability, and other consumers remain unverified.

Without accepted evidence, these known layouts retain descriptive path-candidate roles but keep `level`, loading, and pipeline stage unknown. Custom extensions, wrong layouts, nested resources, non-Markdown children, other homes/profiles/platforms, and `chronicle/notes/` remain unclassified inputs (`extension_unknown` where the extension namespace itself is recognized). Existing general extension `instructions.md` paths retain their official background-guidance role, but their mere presence cannot promote sibling resources.

**Source-location caution:** the retrieved `memories/README.md` still mentions older `core/src/memories/` and `memories/read/` locations. The initially attempted old `core/src/memories/mod.rs` was unavailable. Rule implementation uses the successfully retrieved `ext/memories/src/{extension,prompts,lib}.rs`, `ext/memories/templates/memories/read_path.md`, and `memories/write/src/lib.rs` for current reader behavior and artifact definitions; the README is supplemental pipeline documentation.[7][8][10]

### Claude Code: repository auto-memory, bounded index, on-demand topics

Default auto-memory lives under `~/.claude/projects/<project>/memory/`. The official rule is repository-based sharing across subdirectories and worktrees (or project-root-based outside git). `MEMORY.md` loads its first 200 lines or 25KB, whichever comes first, at conversation start. Topic Markdown files load on demand through file tools. Ordinary subagents do not automatically receive the main conversation's auto-memory; fork inheritance and subagent-owned memory are distinct cases.[3]

Resolver behavior:

- The root index uses conditional `automatic`; other `.md` files under that auto-memory directory use `on_demand`.
- Candidate project paths are **forward encoded** with non-ASCII-alphanumeric characters replaced individually by `-`, then compared with the stored directory token. Only exactly one distinct candidate match produces `level: project` and that candidate in `appliesTo`.
- This encoding match is deliberately labeled `path_inference`, not a documented reversible decoder. A literal `my-project` and the path components `my/project` can collide. No match or multiple matches keeps `level: unknown` and empty applicability, while retaining the documented index/topic loading distinction.
- Never replace all hyphens with slashes. Never manufacture project paths from the token. Repeated copies of the same candidate are deduplicated before ambiguity checks.
- Git/worktree relations are described but not discovered or expanded. No git metadata is read. Custom config homes, `autoMemoryDirectory`, project-directory-name overrides, and subagent memory directories are left unsupported; those options can change the effective location or sharing.[3]

### OpenClaw: agent workspace, not every code repository

OpenClaw's agent workspace is distinct from its state/config directory. The default is `~/.openclaw/workspace`, with named profile defaults under `~/.openclaw-<profile>/workspace` and possible agent-specific `workspace-<agentId>` directories. Configuration, state-directory overrides, multi-agent routing, and sandboxing can change the active workspace; unused directories do not automatically merge.[5]

`USER.md` is workspace bootstrap context with a separate 4000-character budget. The workspace guide restricts long-term `MEMORY.md` to the main private session, not group/shared contexts, and bootstrap budgets can truncate injected copies. Daily notes and imported Markdown are retrievable with memory tools rather than always injected in full.[4][5]

The current memory overview also describes automatic inclusion of today's/yesterday's dated notes on a bare `/new` or `/reset`, including slugged daily variants. The workspace guide describes reading those dates as a session-start recommendation. The resolver preserves that conditional trigger rather than claiming every daily note automatically loads in every session.[4][5]

Resolver behavior:

- Recognized `workspace` / `workspace-<agentId>` paths have `level: workspace`, never user-global, even if the file is named `USER.md`.
- `USER.md` / `MEMORY.md` use conditional `automatic`; Markdown under `memory/` uses `retrieval`, with the dated-reset exception stated in `trigger`.
- Only known projects contained in that workspace are candidate matches. Working on an unrelated external repository with file tools is not evidence that the repository owns or automatically receives the workspace's memories. Profile mismatches fail closed.
- `~/.openclaw*/memory/*.sqlite` (and similar database suffixes) is only a database-path candidate: `level: unknown`, unknown loading, no guessed agent/workspace/project mapping, no table reads. Current builtin-engine documentation instead describes per-agent runtime SQLite storage, so an older `memory/main.sqlite` must not be assumed to be the active engine or prompt content.[6]

## Safety and deliberately unsupported cases

The implementation uses Python 3.9 stdlib and supplied metadata, optionally augmented by bounded instruction evidence. It makes **zero file-content reads, directory scans, configuration reads, environment-credential reads, SQLite queries, network calls, uploads, or writes**. Optional declaration strings are inspected only in memory; resource/note bodies remain opaque. `Path.home()` supplies a default user home only when the caller omits `home`; an explicitly invalid home does not fall back to the current user's files.

Unknown cases remain present and honest: arbitrary memories from other platforms; custom home/config/workspace locations; unrelated local `MEMORY.md`; settings-dependent mappings; unknown extension consumption; imported copies outside the documented OpenClaw memory tree; undocumented memory namespaces; unresolved Claude encodings; unmapped worktrees; actual session type; permissions and enablement; byte-for-byte content, actual selected snippets, and read/use telemetry. The module does not claim a complete effective prompt, real cloud embeddings, or observed retrieval.

## Verification

Tests use isolated `TemporaryDirectory` fixtures under `~/.hermes/cache/scratch`. Each new rule was introduced after an observed failing behavior test and followed by a passing run. Additional regression cases cover category exclusion, profile/platform isolation, unknown paths, encoding collisions, deduplication, filter mismatches, nonmutation, and blocked file/network/database access.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p test_memory_scope.py -v
TMPDIR="$HOME/.hermes/cache/scratch" PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
```

Extension-role verification on the checked date: the focused suite passed **25 tests**. Red→green slices first failed for the missing evidence argument, unrecognized Chronicle/ad-hoc declarations, and absent role/pipeline fields; implementation then passed. Regression cases cover changed text even with a freshly matching digest, truncated/malformed evidence, source-path mismatch, custom extensions, platform/profile isolation, duplicate conflicts, nonmutation, and continued no-file/network/database access **with accepted evidence supplied**.

The concurrent repository run executed **270 tests**, with two failures outside this change's allowed files: `test_frontend_memory_density` cases for local-declaration provenance and unavailable-file previews. No frontend/service fixes are claimed here. Earlier catalog totals are not repeated as current measurements: this resolver does not scan or establish catalog freshness, and deleted/rotated resource entries may remain in a supplied inventory. All observations remain unverified regardless of role classification.

A separate live metadata smoke check enumerated the three supported direct-child trees and supplied only their reviewed instructions: **389 Skysight resources, 10 ad-hoc notes, and 0 Chronicle resources**. Without evidence all **399** inputs stayed unknown; with matching evidence all **399** became user-namespace background input candidates, with roles `extension_resource` (389) and `consolidation_note` (10). Every observation remained unverified, with empty project applicability and `allProjects: false`. The outer smoke harness performed the listing/instruction reads; the resolver did no I/O, and no resource/note bodies were opened. These are live-tree subset counts, not cached catalog totals; rotated/deleted catalog entries explain why inventories can differ.

## Sources

[1] https://hermes-agent.nousresearch.com/docs/user-guide/features/memory
[2] https://hermes-agent.nousresearch.com/docs/user-guide/profiles
[3] https://code.claude.com/docs/en/memory
[4] https://docs.openclaw.ai/concepts/memory
[5] https://docs.openclaw.ai/concepts/agent-workspace
[6] https://docs.openclaw.ai/concepts/memory-builtin
[7] https://raw.githubusercontent.com/openai/codex/main/codex-rs/ext/memories/src/prompts.rs
[8] https://raw.githubusercontent.com/openai/codex/main/codex-rs/ext/memories/src/extension.rs
[9] https://raw.githubusercontent.com/openai/codex/main/codex-rs/ext/memories/templates/memories/read_path.md
[10] https://raw.githubusercontent.com/openai/codex/main/codex-rs/memories/write/src/lib.rs
[11] https://raw.githubusercontent.com/openai/codex/main/codex-rs/memories/README.md
[12] https://raw.githubusercontent.com/openai/codex/main/codex-rs/ext/memories/src/lib.rs
[13] https://raw.githubusercontent.com/NousResearch/hermes-agent/main/tools/write_approval.py
