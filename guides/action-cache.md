# Action cache

The coordinator uses a local **Bazel-style action cache (AC) plus a content-addressable store (CAS)**. The layout and dependency model follow [Bazel remote caching](https://bazel.build/remote/caching); the encoding is `rea-action-json-v3`, not Bazel Remote Execution API protobufs. No Bazel installation or remote cache server is needed.

```sh
make benchmark                         # All configured cells; reuse valid observations
make benchmark BENCHMARK_FLAGS=--offline # Refuse missing inference before provider access
make check-action-cache                # Offline dependency, corruption and migration controls
make check-runner                      # Two cached coordinator passes; no native evaluation or model calls
make attest-runtime                    # Saved offline installed-runtime inventory
make check-attestation                 # Runtime/provider mutation and privacy controls
make regrade                           # Reuse/import grades; execute grading only when semantic inputs change
make reports                           # Global reports from saved data; reuse rendering cache
make pages                             # Public HTML from the sanitized summary
```

## Digests and storage

All digests are SHA-256 over exact bytes. A digest records both `hash` and `size_bytes`. JSON objects use sorted keys, compact separators, UTF-8, and reject NaN. Array order remains significant. Paths are normalized relative POSIX paths; input enumeration order and checkout root are not hashed. A grading trial outside the repository retains its absolute path to keep temporary experiments distinct.

1. Hash input file bytes.
2. Hash sorted directory nodes recursively into a Merkle input-root digest.
3. Hash the canonical command: arguments, declared environment and platform.
4. Hash the action descriptor: encoding version, stage, command digest, input-root digest and timeout.
5. Store the action result in `ac/<action hash>`; it lists output paths, executable modes and CAS digests. Store immutable file/directory/command/action/output blobs in `cas/<content hash>`.

The local root is `.cache/benchmark-actions/v3/`, ignored by Git. Writes are atomic and exclusive. Conflicting observations for the same action fail; missing or corrupt CAS blobs and escaping paths fail closed. Restored outputs are regular copies, not hard links. The cache is private: inference outputs and private report exports must never be published.

## Declared dependencies

[The dependency registry](../benchmarks/config/action-dependencies.json) selects files by stage, harness, condition and language. Only the selected entry contributes inputs; hashing the entire registry would unnecessarily invalidate unrelated stages. Hashes use file contents rather than Git commits, modification times, or recursive script-directory globs. Stat values may accelerate in-process file reading but are not action-key inputs.

| Stage | Effective inputs | Changes that preserve this stage's key |
|---|---|---|
| Capability preparation | Selected declaration, preparation code, original source/header, analysis tools, pinned compiler/binutils and host/Python identity | Models, run budgets/enabled flags, prices, reports; unrelated family source files |
| Inference | Public binary, prompt, tool schemas, manifest, model/reasoning settings, budgets, gateway/tool-policy and selected adapter code, selected evidence-delivery branch and paging settings, recorded runtime settings, trial identity; new tasks also bind installed runtime content and the resolved provider route | Report/tests/README, grading, prices, display labels, scheduling/storage code, the other harness's delivery branch |
| Grading | Unchanged candidate, actual frozen vectors, protocol/evaluator/isolation code, closed compiler/linker/library/Python/executor image digest, kernel/CPU capabilities, language standard, ABI and resource settings, trial identity; Go import checker for Go only | Inference/report/tests/README, scheduling/storage/offline-guard code, prices; Go checker for other languages |
| Accounting | Request-linked ledger and supplied cost calculation, selected model rates/FX, ledger and pricing implementation | Inference/grading/report presentation, other models' rates and pricing metadata |
| Private report | Collected global data and its input/provenance hashes, declared report code, templates/assets, Python executable/version and compression/OS/architecture identity | Unobserved files and cache storage implementation |
| Public report | Allowlisted summary, coverage catalog, renderer/chart code, templates/assets and rendering runtime identity | Private traces, compiler/model installations and unrelated benchmark scripts |

File granularity is intentional: changing any part of a declared semantic source file changes that action. A change to report provenance can change the report while preserving inference and grading. Editing a Go import checker can change report status without invalidating a C++ grade. Output directories and generated reports are never inputs to themselves.

## Existing records and independent observations

Capability preparation uses the same AC/CAS format in a separate action namespace.
It restores immutable packages/specifications and atomically selects their checked
task index. It does not change inference identities or turn reuse into another
sample. See [automatic preparation](capability-preparation.md).

Historical `task.json`, `completed.json`, original grades, candidates and answers remain unchanged. The existing reviewed compatibility bindings are still required for old inference records. Migration verifies those bindings, raw artifact hashes and archived source bytes before projecting the explicitly declared action. It does not grant compatibility to changed prompts, adapters, model settings or budgets.

An existing exact-return grade is imported only when candidate, vectors, language, standard, ABI, compiler, limits, protocol and every newly declared semantic source hash match its verified manifest. Changes confined to the old storage/offline modules no longer force evaluation. New schema-3 derived grade records and selections coexist with every old version. A CAS hit can restore derived artifacts without compiling or running the candidate.

Each logical attempt is part of action identity. A hit reuses an existing observation; it does not count as another model attempt or independent native trial. The inference AC stores the immutable answer and provider receipt; the original task, completion manifest and diagnostic traces remain the audit source. Deleting that source does not authorize a new call: an existing AC entry blocks duplicate dispatch and requires restoring the saved attempt. Derived grading/report trees can be restored directly from CAS. Interrupted/provider-failed attempts still refuse automatic retry. A true inference miss in offline mode stops before any provider launch.

Grading publication stages files in a temporary directory and publishes verified
files individually, using atomic no-replace links on the destination filesystem.
It does not rename the entire directory on a Windows/WSL mount. `grade.json` is
published last, after the artifacts and manifest have been checked; only then is
`selected-grade.json` updated. An interrupted publication can reuse matching
partial files and the existing grading CAS without another compiler or model
run. Conflicting files, symlinks, missing staging files and deleted artifacts
fail closed. The staging context cleans up after publication or failure, so a
missing old temporary directory alone does not establish when it disappeared.

Capability records now distinguish the logical `inference_trial` from their
physical result directory. The logical value keeps the original
`<model>/<case>/<public-package>/<configuration>` encoding, preserving existing
v3 action keys. New physical attempts use the sibling
`<model>/<case>/<public-package>/<inference-action>` directory. A matching saved
legacy action keeps its original directory. A changed effective provider,
runtime, prompt or adapter produces a different action and a separate attempt;
no old record is moved, edited or treated as compatible.

The batch resolves the current local route/action before classifying a ready
cell for registration. Missing preparation is frozen in the schedule first and
resolved before the complete execution binding. The resolved action is checked
again before materialization and dispatch. The persistent shared action claim
and inference AC still block a second call for an identical interrupted,
failed, deleted or already-completed action. Random temporary paths and report
changes do not enter the logical trial or inference action.

Official stop/resume writes use a shared file lock. A batch retains its initial
stop epoch through route resolution, preparation and registration; resume checks
that epoch while holding the lock. Stops are checked again before dispatch, so
a quick stop/resume cannot make an older batch eligible to launch another task.
The empty execution-state lock is persistent coordination data; temporary test
and runtime directories still use automatic cleanup guards.

Global collection reads both layouts without probing installed runtimes or
providers. New logical/physical bindings must match the committed inference
action's trial. Several actions for one configured cell remain separate in
history and expenditure; the cell says `action_selection_required` until an
action is explicitly selected. Collection never chooses the best or newest
score as a substitute for current runtime attestation.

The Makefile defaults to `/usr/bin/python3`. Use that same interpreter for reproducibility checks: different Python versions can accumulate floating-point summaries differently, so report actions separate their runtime identities. Changing the renderer runtime does not invalidate inference or grading.

## Scope and publication

New inference actions bind [local runtime content and the frozen provider route](runtime-attestation.md), with fresh checks before and after execution. Historical observations remain in their original unattested namespace; the reviewed source upgrade bridge preserves their old action digests without asserting runtime equivalence. Unrelated project scripts still do not enter inference keys.

New grades use [pinned compiler/linker/library and executor images](grading-runtimes.md).
Capturing or compressing an image is storage work; verified manifest admission and
the actual execution boundary remain semantic grading dependencies. Native grading
still depends on the recorded host kernel and CPU and does not authenticate return
frames. Inference attestation checks do not provide immutable execution snapshots
or remote model-weight signatures. Historical runtime/provider and request-budget
facts remain unknown; none is reconstructed from today's installation.

Public cache hits still pass the complete file-type, credential/private-path, hard-link, and local-link validation before staging `docs/`. Only HTML/CSS/JavaScript and the empty Pages marker are deployed. CAS, provider JSON, source exports, raw answers and reasoning remain private. Rendering a report never authorizes a model call.


Preparation's bounded metadata-admission memo is an in-process verification
optimization, separate from action caching. It preserves per-load reads, content
hashes, policy and payload/CAS checks. It never supplies a compiler result or
changes an inference identity. Public syntax notices are actual prompt inputs,
so enabling one intentionally selects another inference condition; report changes
and metadata-admission scheduling remain outside those model-input identities.
