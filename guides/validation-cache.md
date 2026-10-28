# Incremental coordinator validation

`make check-capability-coordinator` plans content-addressed actions for individual
checks. It consults the AC/CAS before preparing any isolated workspaces. A fully
warm run starts no workers and performs no SDK preparation. Missing checks run
in isolated Linux temporary workspaces with model launches and network access
forbidden. Temporary workspaces are removed on normal completion or exceptions.

```sh
make check-validation-cache
make check-capability-coordinator TEST_JOBS=4
python3 benchmarks/scripts/check_capability_coordinator.py --plan
python3 benchmarks/scripts/check_capability_coordinator.py --tests test_01_named_blocks_preserve_exact_bytes
```

The last command populates only the selected check; it cannot admit the full
suite. The explicit `make check-capability-coordinator-cold` target retains the
legacy complete regression workflow, including preparation and analysis-context
checks. It is not a dependency of ordinary incremental validation.

## Action identity

Each action binds the check name, the selected test method, shared helpers and
module initialization, declared implementation sources, frozen fixtures,
configuration, pinned SDK manifests, and host Python/Node/bubblewrap identities.
The existing versioned canonical JSON Merkle tree and content-addressed store
are used; this is not the Bazel remote execution protocol.

The three coordinator test modules are projected to the selected check, and
workers execute those same projected bytes. Referenced sibling checks are kept.
Other test bodies do not participate in that action. A changed test therefore
invalidates its action; shared helper changes conservatively invalidate every
check depending on that module. Grader and capability implementation inputs are
currently a conservative shared closure, so changes there can invalidate all
coordinator checks. Unrelated scripts and report code are outside this closure;
report-only entries in the action dependency registry are excluded too.

SDK manifest identities describe pinned images, not a fresh attestation of all
installed runtime bytes. Existing SDK loaders and runtime/provider admission
remain responsible for verifying actual payloads. A passing coordinator cache
cannot bypass an analysis-context or runtime dependency mismatch.

## Integrity and migration

### Explicit baseline plus delta acceptance

`make check-capability-incremental` validates the exact reviewed change set in
`benchmarks/config/incremental-validation.json`. This is an incremental release
acceptance policy, not a claim that all 104 checks ran against the new sources.
The original receipt and all its successful AC/CAS outputs remain historical.
The policy pins that baseline, each changed input's old/new digest, and each
changed projected test body. Unknown input, toolchain, test or helper changes
fail closed. Every changed check must be in the current execution selection.
Focused current checks cover declaration/admission boundaries, immutable cache
handling, grading publication and response/task limits. Their evidence has a
separate action and receipt; old passes are never written under new action keys.
Cached current focused checks are reused. The global report keeps the historical
`coordinator` row separate from `coordinator_incremental`, whose count includes
only the current focused acceptance checks. Registration, analysis-context,
task-family and actual grading gates remain independently enforced.

Updating this policy requires reviewing the new delta and selecting appropriate
checks; changing its hashes alone does not create a passing receipt. Missing or
failed acceptance cannot launch a model. Ordinary full coordinator validation
remains available as a separate explicit regression command.

Only completed passing checks are committed. Cache hits verify AC/CAS output
digests and the check identity. Worker inputs are checked before and after
execution, and the original dependency closure is checked again before results
are published. Paths of temporary workspaces and timing observations are not
action inputs. A new or changed check cannot be admitted using only a suite
summary claiming that it passed.

Schema-2 suite receipts reference the selected check actions. Admission resolves
every current action from the cache rather than trusting a broad source-file
receipt. Prior receipts are archived by digest before replacement. Historical
104-check results are not relabeled with newly introduced dependency identities.
Consequently, the new cache needs an initial population; no full production
population was run during this change. The focused controls exercise a real
isolated two-check run followed by a zero-worker replay, selective invalidation,
shared dependency invalidation, relocation, mutation and corruption rejection.

## Current real-model blocker

The 2026-10-24 pilot ended before provider dispatch because the saved analysis
context expected different bytes for `/usr/lib/x86_64-linux-gnu/libz.so.1.3`.
All four cells remain unprocessed, with zero model calls. Refreshing validation
action caching does not fix or waive that runtime-attestation mismatch. The
failed attempt/status evidence is retained, and no automatic model retry was
performed during the cache implementation.


## Declaration selection and preparation profiling

`selected_entries` enumerates declarations without admitting preparation caches.
Capability rows are `preparation_unchecked`; native rows are `unchecked`. These
are not cache hits or launch permissions. Native attempt metadata is inspected
when its queue cell executes, followed by the existing full cache admission.
Capability inspection remains a distinct `preparation_inspection` stage under
the batch lock, before the full measurement registration/binding barrier. It
shares one inspection per task across models. This change does not remove that
registration barrier or claim that a full typed catalogue starts immediately.

ReadSession reuses common configuration, declarations, and tool metadata within
one process. The known manifest-loader bookkeeping ContextVar is excluded from
keys; fresh metadata scopes no longer defeat identical-input reuse. Caller,
registration and offline control contexts remain part of the key. Nested misses merge their dependencies and validate the union once
before publishing successful child entries. Cache hits still check mutations.
Its bounded (64 MiB) byte cache checks device/inode/mode/size/mtime/ctime; symlink
targets and parent identities remain guarded. It does not use atime, change action
keys, import old validation passes, or cache runtime/provider dispatch checks.

The trace now starts before command import and ReadSession setup. `--detail`
separates buffered reads, SHA256 construction/update/finalization, canonical
serialization, file metadata, code guards, and read-set guards. Leaf events below
1 ms remain in summary counts/totals rather than filling the timeline. Inclusive
and parallel-thread durations must not be added as wall time. Trace writes are
buffered with a 250 ms live-tail flush, and finalized on normal exit/interrupt.

Focused offline commands (no model or build subprocesses):

```sh
make check-selection
make profile-preparation PROFILE_FLAGS='--selection-only --repeats 2' TRACE=auto
make profile-preparation PROFILE_FLAGS='--kinds pointer_probe native_abi matched_buffer --repeats 2' TRACE=auto
```

For a future explicitly authorized benchmark, `TRACE_FLAGS=--detail` enables the
same leaf probes in `make benchmark`. These commands do not run the 104 coordinator
checks. A newly started process still performs initial input admission; later
reuse timing is reported separately from that first inspection.


## Shared compiler manifests

Inside the benchmark ReadSession, `grading.image_manifest.load` now returns one
recursively read-only JSON snapshot per admitted input state. Both the initial
return and subsequent hits use that snapshot. JSON encoding, equality and dict/list
interfaces are preserved; normal nested mutators raise `TypeError`. This is an
application value contract, not a Python sandbox. Explicit `copy.deepcopy` or
`shared_json.mutable_copy` still creates an independent editable tree, as required
by the Clang/LTO image-composition paths.

File, symlink, parent, policy, context and implementation guards still run before
returning a hit. Shared and mutable result policies have different process-cache
keys. Other memoized APIs keep defensive copies, so callers editing ordinary
case descriptions cannot mutate cached state. No action/CAS format or compiler
manifest source bytes were changed.

`--detail` traces outermost deepcopy calls by source function (no arguments or
object content), and separately traces freezing, cloning and cached return. It
does not emit one event per recursively copied element. For reproducible offline
measurements and focused controls:

```sh
make check-read-cache
make profile-preparation PROFILE_FLAGS='--manifests-only --repeats 6' TRACE=auto
```

The code audit also found intentional copies in Clang/LTO composition, request
configuration/attestation sealing, and grading-input normalization. They require
independent writable state and were retained. The three-case preparation probe
measures remaining process-cache copies separately; unexecuted paths do not get
an invented timing estimate.

## Per-inspection metadata transactions

Only top-level `capabilities.preparation.inspect` opens a read-only transaction.
Within that one call, repeated file and parent signatures share the first
observation, including nested cache-hit dependency checks. Before returning or
publishing any new memo entries, the complete dependency union is checked again
against fresh filesystem metadata and directory membership. A detected change
raises an error and discards the result and pending entries. A later inspection
starts a new snapshot; this is not a process-wide assumption that files cannot
change. Other memoized functions keep their existing checks.

CAS reads in this transaction share already verified bytes by storage root and
content digest. Reuse transfers the original read set to the caller, including
when the caller is itself memoized. Fresh exit verification still catches CAS
corruption, symlink replacement and equal-size writes with restored mtime.
Writes through `Path.open` are forbidden inside the transaction; external writes
are checked at the exit boundary. Runtime/provider verification is unaffected.

`--detail` includes `ReadEpoch.observe`, `cas_get` and `verify_inspection` spans.
Use `make check-read-cache` for focused mutation/CAS controls and the three-case
`make profile-preparation` command above to measure first inspection separately
from subsequent process-local hits. These optimizations do not change action
keys, populate validation receipts, run coordinator checks or call models.
# Startup preparation reuse

With `verification_mode: startup_once`, `make benchmark` uses one in-memory
admission scope across preparation inspection, action resolution and binding.
Real validators run on the first use. The batch then trusts those admitted
static inputs: cache hits do not walk their file/dependency guards again.
This scope does **not** claim to detect edits to admitted files during startup.
The normal strict `ReadSession` still detects mutations outside this scope.

Analysis-context evidence is checked for the selected task set once, and its
native closure once per runtime. Binding reuses the resolved per-model task;
provider/configuration, route and fingerprint comparisons remain in place.
Candidate-independent scoring contracts are shared across models of the same
case. Tool-catalog projection is shared by its exact input bytes. Environment
capture now goes through `current_runtime` in resolution, binding and execution,
so those paths share the same startup snapshot.

The scope ends before dispatch and is discarded on success, cancellation or
failure. Cold preparation temporarily leaves trusted admission and clears its
caches before and after creating new inputs; a cached absence cannot hide a new
target. Persistent action IDs and historical validation receipts are not rewritten.
Changes to this implementation still require genuine applicable validation;
these optimizations do not make old source-bound receipts current.

Focused checks and a read-only timing replay use the normal entrypoints:

```sh
make check-preparation-reuse
make profile-preparation-reuse
```

The replay uses six saved C/C++/Rust/Go/ABI task packages, two model labels and
resolution/binding passes. It compares frozen inputs, grading contracts and tool
projections, forbids model dispatch/network connections, and writes valid Chrome
Trace JSON plus a digest-checked summary to
`.cache/benchmark-runtime/preparation-reuse-profile/`. These are **preparation
replay timings**, not full-startup timing or production gate acceptance. No model
is run and no 104-check validation suite is invoked by either command.

For all enabled typed cases and the DeepSeek Flash / GLM models, use:

```sh
make profile-action-resolution
make profile-action-resolution PROFILE_MODE=gates
make profile-action-resolution PROFILE_MODE=components
```

The default `resolve` mode uses real preparation inspection, current admission
receipts, provider selection and runtime capture. It stops before registration
or dispatch. Missing validation is reported as a blocker; it is not replaced
with a simulated pass. `gates` measures the real capability-family admissions
and their same-process reuse. `components` measures saved task-input preparation
with offline fixture mode and explicitly excludes admission and runtime capture.
All modes forbid model dispatch and Python network connections. They preserve
attempts and write separate timestamped directories, closed Chrome Trace JSON
and summaries under `.cache/benchmark-runtime/action-resolution-profile/`.

Public input projections are shared between models only for identical case,
runtime policy and evidence access. Adapter source bytes are shared by the exact
environment/condition identity; action construction still checks their recorded
digests. Verified runtime component blobs are shared only for the same CAS root
and runtime manifest. These process-local caches end with the startup scope;
strict admission outside it still detects file and CAS changes.

The 2026-10-25 full-catalogue replay resolved all 306 actions without dispatch:
inspection 66.30 s, action resolution 273.36 s, and already-resolved reuse
0.000212 s. Within action resolution, 15 family admissions took 62.23 s and
306 prepares took 203.04 s. The latter **includes** 139.91 s for two runtime
captures and 10.00 s for three analysis-context admissions; do not add these
nested timings again. These first-process timings include detailed trace probes.
The separate component replay excludes those admissions/captures: its first
306 prepares improved from 62.81 s to 18.94 s, and its repeated preparation
from 60.24 s to 6.94 s, with identical aggregate action fingerprints.
This is not yet a millisecond full-startup claim. The first input inventory and
runtime capture remain substantial, and isolated validation workspaces still
copy selected inputs/caches rather than using private copy-on-write layers.

