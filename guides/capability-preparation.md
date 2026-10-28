# Capability preparation in the normal benchmark command

Enabled capability declarations now use the existing run/plan entrypoints.
In an initialized, validated environment, a separate preparation command is not
required when an enabled case is added or its preparation inputs change.
The `inference_enabled` flag in `benchmarks/config/capabilities.json` controls
whether capability cells enter model queues. Enabling it does not release
capability claims or waive validation, registration and request-budget gates.

## Bounded diagnostic pilot

```bash
make benchmark CONFIG=benchmarks/config/benchmark-smoke.json
```

This selection uses two existing SysV record-codec scalar tasks, baseline and
counterfactual, with DeepSeek Flash and GLM-5.3: four cells total. It uses the
same coordinator, AC/CAS, immutable registration, grading and global reports
as the full benchmark. Valid completed attempts are reused; interrupted attempts
are retained without automatic retries. A model-queue budget stop prevents its
next case from starting in this diagnostic selection. The full default campaign
uses `execution.budget_stop_scope: case`, continuing other cases after a budget
stop while preserving the failed attempt and unchanged per-case limits.

The user authorized real-model diagnostics on 2026-10-23. Current request policy
allows twelve turns, an 8,192-token response cap on supported capped routes,
a 32,768 task-output threshold and a 200,000 observed-token threshold per task. See
[request budgets](request-budgets.md) for accounting and threshold semantics.
The subsequently authorized GPT Codex OAuth route uses observed task limits
without a provider response cap. One response can exceed its token thresholds;
missing usage stops further requests. It remains a separately labelled budget
condition, with API-equivalent costs and unknown actual subscription debit.
Antigravity remains excluded. Recovery diagnostics
do not replace premises-only ablations or independent task-validity review.
All capability observations remain provisional until their release gates pass.

## Planning and offline execution

```bash
make benchmark-plan
make benchmark BENCHMARK_FLAGS=--offline
```

Planning never builds, restores files, prepares credentials or starts models.
Every selected model/case cell is included, with `preparation_pending` or
`preparation_invalid` when it is not usable yet. `scheduled.scheduled_cells`,
`by_status` and the preparation counts describe the full selected denominator.
Unprepared entries have no guessed attempt directory or borrowed inference result.

The ordinary run acquires the existing batch lock and freezes the selection.
Each model queue prepares its next case on demand, reusing the shared preparation
AC/CAS. Independent model workers may prepare and compile concurrently. Each
model has at most one active measurement, including its preparation, API session
and grading; its next case starts only after that measurement ends. No complete
catalogue preparation barrier precedes model execution. Unselected and disabled cases
are only inventoried, not built. A stopped run still requires explicit resume or
offline mode before preparation can begin. Offline mode can prepare trusted
originals but refuses missing inference before provider work.

Preparation failures preserve the original scheduled denominator, save a failure
receipt with zero provider launches, refresh the report and release the batch
lock. Corrupt immutable data is rejected; an automatic run does not overwrite it
or start a replacement model attempt. Public-package or run-configuration drift
between scheduling and materialization is refused before credentials/provider work.

## Independent preparation action

Both harnesses receive a derived `prompt.txt` containing the task question and
the complete validated public `manifest.json`. Their filesystem tools remain
disabled; a manifest merely present on disk is not delivered model input. The
derived prompt's exact bytes and digest enter the inference action. The frozen
four-file public package and its content identity remain unchanged. The shared
coordinator delivery check and installed-client loopback checks cover this path.

`capabilities/preparation.py` adds a `capability_prepare_v1` action to the existing
Merkle AC/CAS. It binds the selected declaration, relevant preparation sources,
original input/header, analysis tools, compiler/binutils images and host/Python
identity. Run budgets, enabled flags, model routes, prices and report sources are
outside this action; they retain their own scheduling/inference/accounting roles.

Its cached outputs are the four-file public package, private grading specification
and checked task record. A cache hit restores missing outputs without rebuilding
the original or evaluating a candidate. Each immutable path, digest, artifact and
target binding is validated before atomically selecting the task index. Existing
conflicting bytes, escaped paths and symlinks fail closed. Source inputs are
checked again before publication.

A changed preparation source digest means the selected task is pending, not
corrupt. Matched originals, optimized localization and native ABI packages first
verify the saved public/private bytes, then prepare a new action and atomically
select its index. Corrupt bytes remain invalid even when sources also changed.
Old packages and results are retained; a source revision never authorizes a
retry of an identical terminal inference attempt.

Preparation is separate from inference identity. A private-spec revision can
reuse the public package and inference while producing a new append-only grade.
Changing an original or prompt selects a different public package; old packages,
inference records and grades remain available. Existing runtime/provider cache
validation and the refusal to retry interrupted attempts continue to apply.

`make prepare-capabilities` remains an explicit offline preparation/control command.
It uses the same action as the normal run. Preparation snapshots inventory all
definitions, including pending/invalid ones, without building unselected cases.
Reports consume this saved inventory and its source bindings rather than inspecting
tool installations or authenticating accounts.

## Acceptance

`make check-capability-coordinator` checks the normal cold-preparation/warm-inference
path twice with provider launch forbidden, multi-model deduplication, unprepared
planning, restoration, corruption, input/configuration drift, stopped-state guards,
failure receipts and incomplete preparation inventory. These controls are offline
fixtures, not new model measurements. The broader capability measurement release
and family-level reporting gates remain separate. The current source-bound
receipt and global report determine acceptance; an older passing receipt does
not certify later coordinator changes. Task preparation alone does not establish
coordinator acceptance or release a capability measurement.


## Offline control workers

```bash
make check-capability-coordinator TEST_JOBS=4
make check-capability-coordinator TEST_JOBS=1
```

The default is four independent worker processes; one selects sequential
execution. The same setting applies to the existing `make check` entrypoint.
`make check-parallel-controls` checks the worker, snapshot, metadata admission
and result-aggregation safeguards without model calls.

Each parallel worker receives private source/configuration copies, mutable task
indices, a normal default action cache and a separate receipt. SDK payloads are
shared read-only; mutable SDK metadata and runtime state are private. Selected
compiler, preparation and executor actions are copied only after checking their
AC/CAS content closure. Inference, submission and accounting actions are excluded.
Explicitly empty caches remain empty: workers do not install a read-through cache.
The Go vector-revision control establishes its own lower observation-cache
precondition before prohibiting repeated compilation or machine execution.

Worker-local memoization avoids repeated pure admission of identical parsed
compiler manifests. Every load still reads and hashes its selected metadata,
checks policy and retains payload/CAS verification. Modified metadata/policy,
mocked dependencies and direct validator calls are revalidated. This memo ends
with the worker; it does not change inference, compiler or grading action keys.

The parent accepts a combined receipt only after every selected control appears
exactly once, all worker source bindings match, all processes finish and no
control is skipped. Missing, duplicated, stale, crashed or timed-out worker
results cannot produce a passing gate. Per-worker logs and private receipts
remain under `.cache/benchmark-runtime/`; reports publish only bounded gate status.

### Paired premises-only diagnostic

Run the same selected model/case cells in fresh registered sessions while
withholding binary evidence:

```bash
make benchmark CONFIG=benchmarks/config/benchmark-smoke.json BENCHMARK_FLAGS="--evidence-access premises_only"
```

The default command continues to select binary recovery. This flag changes
runtime inputs and the cache namespace only for the explicit control condition;
it does not rewrite cases, introduce a suite, or invalidate existing recovery
actions. The control keeps the original manifest binding, delivers no target
bytes or analysis tools, and uses the same private scoring contract and budgets.
Compare the separate observations through `make reports`. Structured artifacts
have no code-readability score.

### Initialize the already disclosed function entries

The optional `known_entry_seeds` boolean belongs in the `runtime` object of
`benchmarks/config/capabilities.json`. Omission preserves default auto-analysis.
Set it to `true` for the `public-given-entrypoints-v1` analysis condition after
preparing the frozen packages and pinned REA/Ghidra environment:

```bash
make check-analysis-context
make benchmark CONFIG=benchmarks/config/benchmark-smoke.json
```

The coordinator derives anonymous function seeds from each public manifest's
`entry_rva` or `entry_points` and the public ELF load mappings. The prompt also
contains the corresponding absolute source virtual addresses. Localization
receives no seeds or business entry hints. Premises-only sessions receive the
same public address clarification but no binary, seed file, backend override or
analysis tools. Types, layouts, calling conventions and original symbols are
never inferred into the supplied context.

Seed bytes are separate declared Merkle inputs. The effective gateway settings
and fixed-name seed file are checked before and after execution. The source-bound
offline gate exercises the actual MCP/Ghidra gateway and compares its tool
component identities before model dispatch. Missing, changed or unbound seeds
fail before provider access. This uses REA's existing `REA_GHIDRA_SEED_FILE`
configuration; no upstream source change or model annotation tool is required.

The changed runtime configuration creates a distinct attempt namespace and
keeps the existing frozen public packages and stopped attempts. A stopped attempt
is never silently retried. Engine observations and validation results use separate
AC/CAS actions: editing a checker revalidates its assertions while reusing a valid
engine observation. Reports, prices and unrelated scripts are outside those
engine inputs. The policy's v1 format is stable; future policy revisions must
preserve its reader and existing observations.


## Explicit record-role syntax

`runtime.explicit_type_roles` is an optional boolean in the capability startup
configuration. When true, T1/T2 `types.json` tasks additionally state that each
record field's `role` is a single ASCII C-style identifier string of 1..64
characters; `given_facts.path` remains an array of semantic path components.
Other artifact kinds receive no notice. No measured width, sign or layout fact
is included. Omission preserves the historical prompt and cache behavior.

The notice is delivered in `prompt.txt` to both adapters, and its exact bytes
enter the inference Merkle tree. The frozen four-file public package stays
unchanged. Affected analysis-condition names end in `.role-string-v1`; the
registered configuration, task policy and actual prompt are checked together.
Historical submissions keep their original conditions and grades. Premises-only
controls receive the same syntax notice while withholding target bytes and tools:

```bash
make benchmark CONFIG=benchmarks/config/benchmark-smoke.json BENCHMARK_FLAGS="--evidence-access premises_only"
```

A format-edited offline diagnostic is a separate artifact, never a replacement
for a model submission or a capability-release result. Offline acceptance has passed. Paid execution is limited to the explicitly
selected four-cell role-string diagnostic; no full suite or automatic retry is enabled.

## Preparation metadata admission

Preparation and read-only planning use one bounded metadata-admission session.
Every metadata load still reads its selected files, checks content identities
and current policy, and performs its ordinary payload/CAS checks. Only repeated
pure validation of identical parsed metadata is reused. The memo contains at
most 32 identities and ends with the phase. Direct validation and mocked
dependencies bypass reuse. It does not change inference or compiler action
inputs, skip file verification, or authorize an experiment.

SIGINT and SIGTERM request cancellation before waiting for active worker
shutdown. A controlled interruption saves the frozen denominator, stops further
queues, restores the stopped state and signal handlers, and releases the batch
lock. A forcibly killed process can still require explicit orphan-state recovery.
