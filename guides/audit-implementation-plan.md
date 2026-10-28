# Audit repairs and capability expansion

Status: the full configured offline Make command now completes in 258.78 seconds, including global report generation, with zero API calls (2026-10-23). It inspected 984 cells: 925 missing caches, 58 changed-input rejections and one preserved harness failure; no current inference cache hits were admitted. Further paid runs are stopped. Full acceptance on the changed sources, independent review and release remain pending.
The complete plan is not finished. Audited baseline:
`334f5866e636892ea6a71fe07d548319f49e870e`.

The latest warm-path repair defers installed-runtime extraction, rejects saved
harness failures before runtime preparation, continues offline inspection across
the entire matrix, and always generates the global report after that inspection.
Report input bytes are prefetched and reused within the build. Repeated read-only
filesystem queries are shared, followed by fresh metadata and content checks.
Report rendering uses automatically cleaned Linux staging directories with
publication and action caches remaining in the workspace. Saved inference bytes
and inference action encoding were not changed by these reporting optimizations.
Report generation fell from 446.93 seconds to 197.739 seconds. Narrow controls
covered same-mtime content changes, final content seals, absent offline cells,
saved failures, metadata reuse and mutation rejection. No broad regression suite
was rerun. One aborted diagnostic and one 465.81-second completed command preceded
the final measurement, exceeding the requested single measurement attempt.
The [timing page](../benchmarks/site/timings.html) and private logs in
`results/performance-review-20261023/warm-path-final/` retain the actual scope.
This does not establish the performance of a fully populated, currently valid
inference-cache matrix or satisfy the remaining acceptance/release gates.

This document keeps the original repair specifications and nineteen-sub-capability
plan below. Preparation, infrastructure acceptance and measurement release are
separate claims. The user authorized real-model experiments on 2026-10-23.
The first diagnostic pilot selects four existing cells through
`benchmarks/config/benchmark-smoke.json`; this does not release capability claims.
Two preparation attempts stopped before any provider call. The first exposed a
missing public-manifest delivery path; the model prompt now contains the exact
validated premises without changing the frozen public packages. The second
exposed a 55.9 MB runtime binding exceeding the registry's 32 MiB bound. Large
runtime component maps now reside once in the content-addressable store and
enter inference as declared Merkle input files. All entries remain bound; neither
the inventory nor the size limit is weakened. A cached-real-runtime registration
preflight passes with an 80 KB binding and four saved tasks below 45 KB each.

The preceding integrated repair passed 81 coordinator, 38 registration, 31 request,
48 collection, 25 attestation, nine action-cache and 20 public-report controls.
The performance update expands acceptance to 83 coordinator, 39 registration and
41 attestation/storage checks. Its WSL launcher uses temporary Linux runtime trees
under a private mount namespace with unchanged logical workspace paths. Context
manager cleanup handles normal exits, errors and stop signals; owner locks allow
safe removal of abandoned trees on the next startup after a forced kill. Small
selections no longer refresh all 153 preparations or repeatedly scan unrelated
public packages. Validation and before/after runtime hashing remain active.
The expanded acceptance exposed a separate seeded-cache reader defect: replay
and inference publication reconstructed four public inputs while task creation
also hashed `analysis.seeds`. The reader now reconstructs and verifies that
declared fifth leaf, preserving the existing action key; altered seeds fail.
The newly queued premises-only batch was stopped before binding/dispatch with
zero model calls; its registration and all earlier observations are preserved.
See the [temporary execution configuration](setup.md#automatic-wsl-temporary-execution).
The premises-only startup condition is implemented with its own registration:
it physically withholds target bytes and all tools, retains the original public
contract, and stays outside recovery scores. Seven existing DeepSeek recovery action identities remain identical.
The seven GLM action identities change only with the related ZCode adapter fix;
those historical observations are preserved without claiming current-adapter reuse. The first paid diagnostic has now ended: both models
stopped on the first scalar-recovery case at the observed-token threshold, so the
two counterfactual cells were not started and no correctness grades were assigned.
DeepSeek recorded 203,241 total tokens (14,064 output) over seven requests, with
an estimated USD 0.016851174 cost. GLM recorded 239,696 total tokens (12,818 output)
over ten requests, estimated at USD 0.1626043544. Both request ledgers are complete;
these are configured-rate estimates, not observed invoice debits. The threshold
uses usage already observed before a request; the final admitted request can
cross it. The original attempts and their 120 recorded files remain preserved.

Cached traces and an independent offline tool query expose an environment
confound: default Ghidra analysis misses the supplied original caller boundary.
The caller's instructions are present at the correct public address. REA's existing
anonymous function-seed option, using only the public entry RVAs and ELF load
mappings, restores access. The repair passes an actual benchmark MCP/Ghidra
gateway check with zero model calls. Its engine observation is stored in AC/CAS;
a checker-only update reuses the same engine action and creates a new validation
action. It seeds 144 known-entry packages and leaves all nine localization
packages unseeded, preserving every frozen package. Full source-bound acceptance now passes in the main workspace; the policy is
explicitly enabled. The actual main gateway query takes 82.6 seconds under the
unchanged 120-second deadline when run alone. A preceding query timed out while
other offline checks were active; its failure remains preserved. Live diagnostic
tasks run sequentially to avoid that contention. The native cache check distinguishes 47 reusable saved cells from eleven
GLM cells rejected after the related adapter change. Historical grades remain
preserved; changed-input cells are not automatically retried.
No stopped attempt is being retried. Premises-only model observations and
independent task-validity review remain pending; the diagnostic does not establish
a model capability ranking.

## Supplied-entry diagnostic and format sensitivity

The supplied-entry pilot is stopped; no automatic retry is scheduled. DeepSeek
completed the baseline in seven provider requests: 221,476 observed total tokens,
13,828 output tokens and an estimated USD 0.014268192 at the configured rates.
The final admitted request crossed the observed-total threshold and ended
normally. The complete original submission and ledger are preserved. Its strict
official grade is **0/24, invalid artifact**: all four record-field `role` values
are singleton arrays, while the validator requires identifier strings. The
delivered public prose lists `role` but does not explicitly state that value type.

An offline diagnostic changes only those four singleton arrays to their existing
string elements. The resulting description passes **24/24** original-endpoint
checks in both directions, with host-emulator return evidence and zero model
calls. This is a format-sensitivity result for an edited diagnostic artifact,
not the model's official score, and does not replace its original grade or
prove an independent capability release. A zero strict score in this case does
not establish failure to recover scalar facts.

ZCode/GLM fails before its client or provider request starts: its nonfixture
launcher requests the seeded runtime environment without the bound task input
directory. Both counterfactual cells have zero provider requests; one was
interrupted in prelaunch verification and the other remained unstarted. The
coordinator process and model clients are confirmed absent. The stop flag, stale
lock and interrupted status were recovered from the preserved launch evidence.
Main cache/report verification has passed, and the explicitly selected four-cell
role-string diagnostic is now running through the existing Make entrypoint. The
nonfixture ZCode path now passes the bound input directory and validates seeds
before opening its recorder. An actual installed-client loopback regression
verifies that path and public-format delivery without contacting a provider.
The optional explicit role notice is a distinct registered prompt condition.
A real-process SIGINT control also verifies prompt cancellation, stopped state,
signal-handler restoration, durable interrupted status and lock cleanup.

## Corrected role-string diagnostic

The four-cell registration is frozen as `e7be7f27c793c8f331969219474ebba002c70a0cb1055b538c34a46b576663ea`. Both baseline model sessions ended at their request budgets; the two counterfactuals remain unstarted and are not automatically retried. DeepSeek completed eight provider requests: 211,417 reported total tokens and 12,361 output tokens, with a configured-rate estimate of USD 0.02638104. Its next request was refused by the observed-total threshold. GLM completed seven requests: 122,702 total tokens and 27,722 output tokens, estimated at USD 0.1867758702. Its next 8,192-token response allowance would exceed the remaining cumulative output budget. Both usage ledgers are complete, while actual invoice debits remain unknown. Neither submission produced a graded artifact; these are budget stops, not correctness observations.

DeepSeek made twelve tool calls without reported tool errors. GLM made eight calls with two argument errors: an inadmissible target path and an unmapped procedure address; caller/callee decompilation succeeded. No additional experiment or retry was started to diagnose those cached errors. DeepSeek's estimate uses the saved peak-hour rates for these recorded request times; earlier off-peak estimates are not directly interchangeable.

The report collector now gives an explicitly certified budget stop precedence over the adapter's generic failure marker. Runtime/provider drift and malformed request certificates still remain environment failures. All 48 collection controls and 20 public controls pass, and replaying the actual record changes no saved output or grade. These report-only sources are outside both live inference profiles, runtime-control dependencies and registration policy sources.

## Current implementation inventory

### Latest performance repair and stopped pilot

The preceding **99/99 coordinator pass is historical evidence** for the sources
that produced it. Subsequent changes to the runner, coordinator, registration
and runtime attestation have not received another full source-bound acceptance
run. The current focused checks pass **26/26**: twelve routing/cancellation and
timing controls, four registration controls, five runtime controls and five
report controls. The 22-check analysis-context receipt was refreshed from the
cached engine observation (`engine_cache_hit=true`), without a model call.

Failed, cancelled and incomplete batches now save status and exit without
archiving profiles, auditing all conditions or rebuilding the global report.
Successful batches retain one global report build. Stage timings are saved
outside action identity; periodic progress writes only the lightweight status.
Capability-only selection skips unrelated native attempt inventory, and scoped
runtime reuse avoids repeated capture before dispatch while retaining fresh
before/after-dispatch verification.

The production entrypoint's four-cell **offline coordinator measurement took
95.3 seconds and made zero API calls**. It ended with the expected missing-cache
failure and skipped finalization. This measurement excludes temporary-runtime
extraction and live fresh pre/post runtime verification. It does not establish
an end-to-end live speedup or a completed warm/full acceptance gate.

The latest paid diagnostic was stopped. DeepSeek attempt `62b12…` completed one
HTTP 200 request in **51.602 seconds**, reporting **1,551 input + 8,192 output =
9,743 tokens**. Its output remains invalid and ungraded. GLM was cancelled during
runtime verification before any provider forwarding. The existing attempts are
preserved, further paid runs are stopped, and the paired premises-only result
remains incomplete. One global report rebuild completed in **446.93 seconds**:
private report `8f17d021cf67`, sanitized public report `d6b27aca5a6d`.
The local site and `docs/` contain 45 static HTML/CSS/JS files each, with the
offline stage timing table. No remote deployment is claimed. Report collection
and generation remain a separate, substantial delay; the complete live command
has not yet been verified against an end-to-end timing budget.

### Premises-only pilot and execution-environment repair

The four selected premises-only cells were registered as
`7f7b425840e617074de1f850605b9f7112e602dc21e847d49da840804705045d`,
with binding `9ce07799192240ba3e1576f5379e3a5d1caddbfdbaed6fd3aa2b5bf9cc4a2417`.
DeepSeek's first response used 1,548 input and 8,192 output tokens (9,740 total)
in 57.038 seconds, with a configured-rate estimate of USD 0.0051474. Its saved
turn-end event is `max-tokens`. The client also recorded an MCP startup error,
and its original environment-failure classification and ungraded artifact are
preserved. The per-response limit event does not certify exhaustion of the
32,768-token cumulative task budget.

The batch was stopped while GLM's first HTTP response was in progress. Its
partial stream has no reported usage; its token total and bill remain unknown.
Both counterfactual cells remain unstarted. The two original attempt trees are
retained byte-for-byte (24 files), with no automatic paid retry. The global
history includes both attempts separately from recovery measurements. Neither
supplies the paired, assessable ablation result still required for release.

Offline syscall tracing established that MCP startup failed while opening
`/dev/null`: the root bind's device restriction yielded `EACCES` before the child
attempted `execve`. Node reported that setup failure against its executable path.
The launcher now mounts a private standard `/dev`. A direct null-stream and
nested-child regression passes; all 31 installed-client/request controls pass
inside the fixed temporary namespace in 36.442 seconds, without provider calls.
Premises-only DSH also omits and explicitly disables the unused REA connector.

The execution-storage launcher and its configuration are now explicit runtime
attestation inputs. All 41 runtime/storage controls pass in 5.768 seconds, and
all 39 registration controls pass in 122.486 seconds. Unrelated source and random
temporary-name invariance remain covered. Related runtime changes receive new
identities; old observations are not retroactively certified or repeated.

The independent-review handoff was checked against the current frozen inventory:
101 recovery packages, 404 exact public input files and all 19 capability labels.
The 52 full-semantics generation packages remain withheld, and the delivery
supplement matches the exact role-string notice. This verifies handoff integrity;
the user-arranged independent review remains pending.

The existing coordinator now has **153 configured task definitions** in the same
six program families. This includes **72 matched compiler/language conditions**:
six families × three original compilers × C/original-language output ×
recovery/full-semantics generation. Target conditions use pinned Clang 20 at O2,
Rust optimization level 2, and Go compiler-default optimization. The pre-existing
GCC conditions remain. These are related testing conditions, not 153 independent
families or additional named capabilities. Two additional T3 conditions exercise
two-level pointers under paired layouts. The main preparation snapshot now contains all 153 ready definitions. The normal
preparation command revalidated their cached actions after the parser repair.
All 153 frozen task records and public packages remain unchanged. No compiler,
emulator or provider was invoked by this cached preparation pass.

The saved original-build audit verifies every declared task against its selected
compiler, source language, optimization and stripped target: 77 GCC O2, two GCC
O3, 24 Clang O2, 25 Rust optimization-level-2 and 25 Go default-optimization
conditions. The global report check validates this selection per task; it does
not assume a fixed GCC count as the declaration matrix expands.

The current preparation verifies all 153 packages and private-spec bindings,
131 frozen source bindings, and zero model calls. All 151 preceding task identities
remain identical. The matched baseline matrix passes 16 controls,
restricted Rust candidates pass 17, and restricted Go candidates pass 18. The
matrix compares 1,026 original/GCC/Python observations; Rust and Go each include
342 original-language gold observations. The new restricted paths do not claim
general heap, GC, unwinding, syscall or runtime-startup support.

The expanded common coordinator suite passes **all 81 checks**, with 33 current
source bindings and normal cache semantics. Four isolated workers cover 21, 20,
20 and 20 unique checks. The preceding sequential 70-check run also passed and
its original receipt, source closure and log are preserved. Registration passes
38 controls with the normal cache; typed collection passes 48; public reporting
passes 20. These checks use fixtures, compilers, emulators and cached traces,
not real models. Source-bound local receipts and the generated global report
determine current acceptance.

Matched grade manifests retain family, original build, output language and
original compiler, and the grading action binds that selection. Missing matched
fields are rejected before private grading. The full coordinator checks cover
all four output languages and grader-only revision reuse. Older grades and the
preceding failed diagnostic receipts remain unchanged.

The four-condition pointer scope passes 27 source/runtime-bound controls.
ABI (35), optimized-location (42) and C++ buffer (16) receipts were accepted only
after comparing exact integrated source hashes and runtime pins. The current
48-check collector passes; the public renderer passes 20 isolated controls.
The full preservation scan verifies all 7,541 historical files unchanged.

Capability inference was enabled for the recorded diagnostic. The syntax and ZCode corrections have passed offline acceptance. Capability
inference is enabled for the explicitly selected four-cell role-string diagnostic;
there is no full-suite launch or automatic retry.
The initial selection is DeepSeek Flash and GLM-5.3 on the paired record-codec
SysV scalar conditions, four cells in total. Each task permits at most twelve
model turns, 8,192 output tokens per response and 32,768 cumulative output tokens,
with a 200,000-observed-token stop threshold and no automatic retries. GPT's
currently selected OAuth route remains unsupported for enforced output caps;
no provider fallback is permitted. Antigravity remains excluded.
**0/19 capabilities are released.** These binary-assisted recovery diagnostics
are not the still-required premises-only ablations.
The latest native cache check preserves all 58 saved cells among 66 configured
cells. After the related DSH adapter change, all 58 differ from current declared
inputs; none is silently certified or rerun. The preceding source version had
47 current-input hits and eleven changed-input GLM rejections. Current controls
also verify positive cache creation/reuse with one isolated, unpaid fixture and
zero provider launches, including when no historical action matches current code.
Its eight incomplete DeepSeek cells remain visible: one
infrastructure failure and seven unstarted cases. This capability pilot does not
retry those historical native cells.

The preceding offline report verification produced two byte-identical fresh renders
and a matching cache restoration: 523 artifacts, 96 HTML pages and 1,544 portable
links. The normal `make reports` command produced private build `0216202ba7c1`
and public build `5e4f7dcc2189`; all 45 public site and local `docs/` files match.
Verification and publication made zero model calls. The corrected diagnostic
starts after that frozen snapshot and has separate saved records.

Use the existing entrypoints documented in [capability preparation](capability-preparation.md),
[matched baselines](matched-baselines.md), [registration](capability-registration.md)
and [global measurements](capability-measurements.md). There is no per-model or
per-work-package experiment runner. `make reports` generates the global view
from versioned records and accepted summary inputs without calling a model.

The corrected reader checks analysis flags in the attested harness configuration
and verifies it against the original effective-configuration Merkle input. It
does not infer those flags from the older four-field runtime projection. The
existing paid DeepSeek observation is readable without changing its files. Its
invalid artifact is reported as not executed, rather than using an old nominal
host-return tag as evidence of a machine call. Numeric scores remain unchanged.

All 153 exact task records were revalidated in the isolated source snapshot. A
cache handoff identified exactly 153 current preparation actions whose public
files, private specifications and records match the main frozen data byte for
byte. Main revalidation uses those entries through the normal preparation
command; no new benchmark, original program or inference result is introduced.

## Completion audit

| Work item | Implemented behavior | Remaining boundary or acceptance gate |
| --- | --- | --- |
| R1 exact values | Shared byte parser, numbered calls and append-only offline regrading for existing tracks; CR, LF, NUL, invalid encoding, missing/duplicate/extra calls, noise and early termination controls | Same-process frames retain the R6 provenance limitation. |
| R2 Go imports | AST inspection of decoded imports; comments, aliases, grouped/raw/escaped imports and syntax errors have separate controls | Future multi-file artifact admission is outside the current single-file contract. |
| R3 request budgets | Shared serialized-request guard, response caps and session-wide turn/token accounting on supported new routes | Unsupported routes fail before forwarding. Historical enforcement remains unknown where unrecorded. |
| R4 runtime/provider | Effective route resolved before inference hashing; pre/post component drift checks; closed compiler/linker/library/Python/executor images | Current installations cannot certify missing historical identities. |
| R5 usage | Request ledger preserves explicit incomplete/partial/unknown usage; derived accounting revisions remain separate from task success | Missing historical usage and actual invoices remain unknown. |
| R6 authenticated returns | Restricted host-owned C executor and separately controlled C++/Rust/Go buffer paths; forged stdout, exits, memory faults and caller corruption are rejected within the exercised profiles | General historical native C++/Rust/Go frames remain unauthenticated; restricted controls do not retrocertify them. |
| Action cache | Separate declared Merkle actions and AC/CAS for preparation, inference, compilation, execution, grading and reporting; creation/reuse survives temporary-runtime cleanup. Capability physical storage now uses an inference-action sibling, with the original logical trial encoding retained for old keys | Offline routing, registration and collection acceptance is being refreshed. Changed actions preserve old attempts; the same stopped action remains nonretryable. Multiple saved actions in a configured cell require explicit selection in the global report. |
| L1–L6 | Closed artifacts, hint-free packages, exact-binary semantic instruction reviews and optimized-location diagnostics | Independent alternatives/completeness review, broader patterns and measurement release remain open. |
| C1–C6 | Matched C/native buffer conditions, explicit callbacks/lifecycle and interleaved persistent-state conditions through the existing coordinator | Earlier coordinator acceptance is preserved; full acceptance of the latest performance changes, independent validity, broader interfaces and release remain open. |
| T1–T7 | Submitted descriptions drive bidirectional probes retaining original endpoints; bounded integer records, pointers, variants, indirect calls and native-language ABI diagnostics | Broader types/topologies/signatures, independent validity, premises-only model ablations and release remain open. |
| P7 release | Immutable schedules and complete pre-dispatch runtime/provider/build/scoring bindings; typed history, denominators and family/lineage macros; global reproducibility/privacy and local publication verified offline | Independent validity and model ablations remain release gates. No capability measurement is released. |

Phase status: **P0 partial; P1 complete for existing tracks; P2 complete for
supported new paths; P3 complete for the restricted prototype; P4–P5 in progress;
P6 diagnostic preparation in progress; P7 partial.** The complete original phase
contracts remain below. A passing local control establishes only its exercised
behavior, not general reverse-engineering ability.

## Preparation performance finding

A stack-only snapshot of the active four-cell diagnostic on 2026-10-23 identifies
repeated compiler-image metadata admission during the full 153-definition
`pin_snapshot` refresh as a pre-provider CPU bottleneck. The sampled call path is
`prepare_selected → pin_snapshot → preparation.inspect → validate_frozen →
cached_original → image_manifest.load → validate_record`. The process was active
at roughly 76% CPU; no new provider request had been sent at that observation.
The running sources remain unchanged. This is preparation overhead, not model
reasoning time.

A follow-up optimization should admit identical metadata once per bounded
preparation session, keyed by its exact content and policy, while retaining each
load's reads/hashes and all payload/CAS checks. Worker-only metadata admission
already has controls for mutation, policy changes and direct-validation bypass.
The production preparation/planning scope passes the 81-check coordinator
acceptance and 34 shared-admission controls. Every metadata load still checks
content identity and policy; payload/CAS checks remain active. Seven unaffected
DeepSeek action identities are unchanged. The seven ZCode identities change
only because of its intentional adapter correction.

## Remaining capability and release gates

### Validation execution and independent review

The user will arrange the independent task-validity reviewer. Review remains
pending until findings are received and resolved; author controls do not supply
that sign-off. The initial handoff should contain only the four-file public
packages, labelled by their opaque public task IDs. Keep private task indices,
truth, original source and full-semantics generation controls separate until the
reviewer has recorded recovery findings. Record prior exposure and do not claim
a blind review when the reviewer has already seen the corresponding answers.

The review record must identify its reviewer, date, covered capabilities and
tasks, exact public-package/binary/build digests, scoring specification and code
versions, evidence, equivalent valid alternatives, incorrect controls, unresolved
issues and final decisions. C1–C6 review includes error behavior, allowed memory
effects, aliasing, callback order, cleanup and interleaved instance state. The
localization and type/ABI procedures linked below provide their additional
criteria. Premises-only execution is implemented as a separate registered
condition. Its paired model observations remain pending; implementation and
model authorization alone do not establish that measurement gate.

The initial local handoff is prepared: 101 unchanged public recovery packages
(406 archive members including instructions and the digest index), covering all
nineteen capability labels. The 52 full-semantics generation packages and all
private specifications, hidden vectors, source and model outputs are withheld.
Archive SHA-256:
`7a6a0223428bdc2ad1c919e7ed22916a70bfc240b8fa042339f409cc27fa2dc7`.
This is a handoff artifact, not a completed review or a release decision.

Parallel acceptance execution is integrated into the existing Make entrypoints.
`make check-capability-coordinator TEST_JOBS=4` and the coordinator portion of
`make check-cold` use four processes by default; `TEST_JOBS=1` selects sequential
execution. Each worker has its own mutable workspace and receipt. All 35 worker,
cache, failure and metadata-admission controls pass. The full repaired suite
passes all 83 distinct checks with matching source bindings and zero provider
launches. Old results remain preserved and inference profiles remain identical.

Routine verification is now separate from that regression workload: `make check`
verifies the prepared action/CAS records and saved artifact bytes using existing
acceptance receipts, without constructing or grading fixtures. It records stage
durations and has a 180-second budget. `make check-cold` explicitly owns the full
regression suite and saves setup/command durations. The global diagnostics report
renders the saved warm measurement and its dependency freshness; it does not
reclassify old model results or claim measured model/Ghidra speed improvements.

The delivery check verifies that both adapters' hashed prompt contains the exact
validated public manifest, including given facts and artifact bindings, across
seven task types. Actual installed clients preserve that text in outgoing
loopback requests. The stopped-CLI control now creates and restores its own stop
state rather than depending on whether the author's workspace is running a batch.

A whole Go grade cache hit need not include lower per-invocation observations.
The vector-revision control establishes its own three submitted-artifact
observations before prohibiting repeated compilation and execution, so it is
independent of test order and partial cache seeds. Workspace seeds include C
compile and Go observation actions, while explicitly cold caches remain cold.

Worker-local memoization avoids repeated pure admission of identical parsed
compiler metadata within a validation session. File reads, content identities,
policy checks and payload/CAS validation remain active. Direct validator calls
and mocked dependencies bypass the memo. A like-for-like Go inspection produced
identical serialized results at 2.92 seconds without the memo and 0.78 seconds
on a warm memo. Compiler, grader, preparation and inference implementations and
action identities remain unchanged.

### Outstanding gates

1. Independently review localization's alternative legitimate roots and semantic
   instruction regions. The codec error-initialization anchor and omitted XOR
   are repaired; required operations and permissible surrounding instructions are
   checked separately. That coordinating-agent review does not establish that
   every valid realization has been identified or accepted. Broader dispatch,
   data-relation and reasoning-chain patterns remain pending.
2. Complete premises-only model ablations and independent T task-validity review.
   Paired scalar/layout counterfactuals preserve nonmeasured public premises;
   fixed baseline/counterfactual descriptions fail in both original directions.
   Deterministic controls do not prove a task requires reverse engineering or
   that no guessed equivalent description exists. Real models are now authorized;
   premises-only execution is implemented with empty target bytes, an empty tool
   catalog, a separate frozen registration and verified input hashes. The paired
   real-model observations remain incomplete; the four-cell DeepSeek
   Flash/GLM-5.3 pilot is stopped. The remaining review procedure is in
   [type/ABI validity gates](type-abi-conditions.md#remaining-scientific-validity-gates);
   it separates independent sign-off from the author's deterministic controls.
3. Preserve the accepted reproducible global publication of the matched
   Clang/Rust/Go conditions while adding diagnostic observations. All-family
   declarations and frozen packages are present; earlier coordinator,
   registration, collection and public-renderer acceptance is preserved. Full
   source-bound acceptance after the latest performance changes remains pending. The offline
   fresh-render/cache-restoration and local Pages publication checks passed.
   Recovery and full-semantics generation use independent sessions with the same
   original binary, invocation contract and hidden inputs. Keep the existing
   O0/O2/O3 compiler expansion build-only until separately registered for inference.
4. Extend bounded T3/T4 interfaces and review T6 indirect/object-call validity.
   Pointer topology, active variants, subobjects, receivers, virtual slots and
   adjustment thunks need observable original-endpoint checks, not two mutually
   consistent generated declarations. C5/C6 callback and persistent-state
   validity, broader interfaces and release review remain required.
5. Independently review L6/T7 validity and expand the declared advanced scope.
   L6 has real O3/LTO originals, byte-bound realizations and a closed instance
   grader. T7 has distinct Rust-native/Go ABIInternal originals and bidirectional
   descriptions; stable C FFI is not native-ABI success. Broader native signatures
   and runtime mechanisms remain pending.
6. Finish P7 without retrocertifying history. Preserve candidates and old grades;
   freeze selection, budgets, identities, scoring, family/lineage weights and
   denominators before new measurement. Verify zero provider calls on cache-only
   execution and reproducible global/public reports. Keep infrastructure-invalid,
   unstarted, unsupported and budget-exhausted cells distinct. Independent task
   validity, model ablations and the remaining publication gates stay open.
7. Complete acceptance of the implemented attempt-storage repair. A capability
   cell retains its original logical trial encoding while new physical attempts
   use their inference action hash. Current runtime/provider resolution precedes
   registration classification; changed actions coexist without changing
   unrelated settings or overwriting old attempts. Old keys, cache hits and
   single-use claims are retained. Global collection verifies the trial from
   the committed action and shows multiple actions separately. A stopped action
   with identical inputs remains nonretryable. This storage repair does not
   authorize paid retries. The earlier coordinator acceptance passed; the latest
   source changes have focused controls only. Full acceptance, broader warm-path
   timing and completion of the global report refresh remain pending.

The pre-correction 92-check run passed 91 checks in 2,191.952 seconds and exposed
one cancellation-ordering failure. The correction honors preparation stops before
new route resolution, serializes official stop/resume writes and retains the
original stop epoch through synchronous dispatch checks. The original failing
control now passes; all fifteen routing/stop controls, forty-two registration
controls, fifty-one collection controls and native cache-boundary controls pass
on those corrected sources. The source-bound isolated-worker run then passed
**99/99 checks**, with zero failures and zero model calls. Its receipt is
`benchmarks/config/capability-coordinator-validation.json`; the final aggregate is
`.cache/benchmark-runtime/coordinator-controls-7e1exk2b/result.json`. Source hashes
matched when that run completed. The subsequent performance changes mean this
receipt does not certify the current sources; the full gate has not been rerun.

After renewed authorization to use real models, the existing `make benchmark`
entrypoint started the four-cell premises-only pilot using
`CONFIG=benchmarks/config/benchmark-smoke.json` and
`BENCHMARK_FLAGS="--evidence-access premises_only"`. It selects DeepSeek Flash
and GLM-5.3 on the existing scalar-recovery baseline and counterfactual cases.
That pilot is now stopped with the partial observations recorded above. Earlier
failed and interrupted attempts remain preserved; paired ablations, independent
validity review and full capability release remain open.

## Sub-capability completion checklist

Every row requires the shared task-validity, frozen-denominator and global
reporting gates. All nineteen retain **unreleased** status.

| Capability | Implemented preparation | Still required |
| --- | --- | --- |
| L1 root localization | Complete scalar-root contract, distractors, absent target and top-1 scoring | Independent alternative-root review and broader baselines |
| L2 semantic sites | Byte-bound instruction reviews, required/context scoring, incorrect-anchor and omitted-XOR regressions | Independent semantic-realization review and broader baselines |
| L3 dispatch | Condition/site/target artifacts and indirect-edge controls | Broader dispatch patterns and independent validity |
| L4 data relations | Retained salt/table references and access-direction controls | Broader objects/ranges and independent validity |
| L5 reasoning chain | Bounded directional nodes/edges and wrong/reversed-edge controls | Broader chains and independent validity |
| L6 optimized locations | O3/LTO originals; inline/clone/cold/thunk/LTO instances; hint-free packages and closed matching grader | Independent completeness/alternative review and release |
| C1 numeric semantics | Matched C/native buffer conditions with width, sign and integer-boundary controls | Broader independent families and validity/release review |
| C2 control flow | Ordering, loop, boundary and equivalent-algorithm controls across matched baselines | Broader independent algorithms and validity/release review |
| C3 parsing/serialization | Exact status, length, bytes and malformed/truncated inputs | Broader parser families and validity/release review |
| C4 memory/aliasing | Host-owned writes, preserved bytes, overlap and capacity errors | Broader memory interfaces and validity/release review |
| C5 callbacks/lifecycle | Explicit callback oracle and matched C17/C++20 recovery/generation; failure and lifecycle effects | Independent validity, broader baselines and release |
| C6 persistent state | Interleaved contexts and candidate-owned prior state; reset/destroy/recreate controls | Independent validity, broader baselines and release |
| T1 scalar representation | Integer-record submitted-type probes retaining original endpoints in both directions | Broader scalar kinds, premises-only ablations and independent validity |
| T2 aggregate layout | Record/nested-array layout probes and observable equivalents | Broader layouts, premises-only ablations and independent validity |
| T3 pointer/object roles | Paired layouts; descriptions drive pointer/extent/alias access in both original directions | Broader pointer topologies, validity, ablations and release |
| T4 variants/subobjects | Paired active-union/subobject originals; strict artifacts and closed original-endpoint probes | Broader layouts, validity, ablations and release |
| T5 call ABI | Integer-record SysV/ms_abi caller/callee probes | Broader signatures/platforms and independent validity |
| T6 indirect/object calls | Paired real virtual calls, thunks, callback/context relations and strict calls.json probes | Broader interfaces, validity, ablations and release |
| T7 native-language ABI | Pinned Rust-native/Go ABIInternal originals, calibrated references and bidirectional aggregate grading | Broader signatures/runtime mechanisms, validity, ablations and release |

## Historical evidence and preservation

Existing language-reconstruction measurements remain historical results with
explicit limitations. They do not gain scores for the nineteen planned skills.
The closed-runtime migration selected 97 exact-return grades without new
inference: 89 have worker-image execution evidence and eight reject before
execution. The migration preservation receipt covers 7,541 original files and
110 published summary rows; subsequent checks must retain them. Saved grading
and accounting revisions remain append-only. Missing usage, generation limits,
provider identities and general native return authentication stay unknown.

Earlier 37/49/61/71/73/75/79-task inventories and smaller coordinator gates are
implementation milestones, not today's denominator or current acceptance.
The present preparation preserves all 79 preceding records. The earlier
localization correction intentionally revised seven localization packages while
retaining old package bytes; it did not silently rewrite historical inference.

The additional review repaired output-language aliases, optional-suite routing,
capability-selection grading identity, malformed private-spec admission and
post-plan profile disablement. Malformed buffer/lifecycle artifact framing now
retains invocation counts and not-executed provenance. None of these repairs
licenses a provider call or replaces the independent release gates above.

The following sections retain the original audited findings, specifications,
program-family plan, data contracts, cache rules and acceptance requirements.
Findings at the old baseline describe historical behavior, not a claim that the
repaired implementation still has those defects.

## Findings at the audited baseline and evidence limits

| Repair | Current implementation and preparation evidence | Historical impact still to establish |
| --- | --- | --- |
| R1: exact return values | [Complex grading](../benchmarks/suites/complex-native-v1/scripts/grade.py) uses text subprocess output and `splitlines()`. Exercising the actual grader with mocked compile/run results accepted all **3,088/3,088** expected strings with an incorrect appended CR. A three-call `A\nextra`, `B`, `C` control scored 1/3 instead of 2/3 and shifted the correspondence. | Which unchanged saved candidates receive different grades after an exact-byte regrade. The preparation control mocked process execution; it was not an end-to-end native execution test. |
| R2: Go imports | [Basic grading](../benchmarks/scripts/grade.py) and complex grading scan the whole source with a regular expression. The current expression rejects a comment mentioning `"unsafe"` and misses a raw-string import of that package. | Which submissions were incorrectly rejected, or passed policy inspection despite a restricted import. A missed import is a policy gap, not evidence of a successful exploit. |
| R3: effective budgets | [DSH adapter](../benchmarks/harnesses/deepseek-harness.mjs) sets model `maxTokens`. The saved [transport probe](../benchmarks/suites/complex-native-v1/config/transport-budget-validation.json) records the Codex adapter omitting the wire output cap. [ZCode](../benchmarks/harnesses/zcode.py) already checks its outgoing cap before forwarding. Preparation inspected that evidence; it did not repeat the transport probe. | Whether each historical request actually enforced the declared limit. A small final answer or a documented model maximum does not certify a requested experiment cap. |
| R4: runtime and provider identity | [Environment profiles](../benchmarks/scripts/environment_profile.py) bind selected adapter/config sources. In a temporary shadow tree, adding a changed `bin/rea` left the profile unchanged; changing the tracked gateway changed it. DSH selects its credential-dependent provider after task preparation. | Actual historical runtime/component identities where receipts did not record them. Current installations cannot supply missing historical facts. |
| R5: usage completeness | [Record aggregation](../benchmarks/scripts/records.py) can replace explicit incomplete usage using exit/timeout or observation counts. Evaluating both current non-GLM cost expressions with `usage_complete=false, timed_out=false` produced `true`. | Correct completeness and cost intervals for each saved request ledger. These expression controls do not establish how many historical rows change. |
| R6: return provenance | [Numbered frames](../benchmarks/suites/stl-native-v2/scripts/grade-framed.py) preserve bytes but are emitted inside the candidate process. Its parser accepts syntactically valid manufactured frames. | Native end-to-end adversarial controls and a validated executor are required. The parser control alone does not demonstrate exploitation of any historical candidate. |

A trusted `/usr/bin/true` started successfully under a minimal `bwrap` isolation
smoke check on this WSL host. This does not validate the full compiler sandbox,
resource limits, executor or a different host. No Unicorn, Wasmtime or Qiling
Python package was found during preparation. The subsequent R6 implementation
locally pins Unicorn 2.1.4; its current status and limits are listed above.

## Implementation order and acceptance gates

Each work package must leave the existing coordinator usable, keep original
receipts immutable, and include offline acceptance controls. Do not start paid
experiments to validate infrastructure changes.

| Package | Changes | Completion gate |
| --- | --- | --- |
| P0: contracts and controls | Freeze return, request, usage, identity and artifact schemas; add reviewed positive, equivalent and single-defect controls. Capture hashes of the historical artifacts selected for migration. | Every claimed failure has an executable regression control; all new task definitions are disabled for inference. |
| P1: R1, R2, R5 | Shared exact-value grading; AST-based Go policy; request-ledger usage accounting. Add append-only grading and derived accounting records. | CR/newline controls fail/pass as specified; Go syntax and import policy are distinct; explicit incomplete usage survives normal exit. Offline changes show old/new deltas without changing candidates. |
| P2: R3, R4 | Certify final provider requests, freeze effective provider/runtime identity before task preparation, separate inference and grading cache validation. | Missing/inconsistent caps or runtime manifests fail before forwarding. Runtime/provider mutation tests invalidate inference; grader/report-only changes do not. |
| P3: R6 prototype | Host-controlled restricted C execution and adversarial executor controls. | Guest stdout cannot supply return records; early exit and invalid memory effects fail; real returns and allowed writes pass. Unsupported programs are explicit. |
| P4: first capability tasks | L1–L5, C1–C4, T1/T2/T5, from a small number of program families. | Each scoring claim has a directly checked artifact and positive/equivalent/negative controls. Hidden-label and public-package leak checks pass. |
| P5: state and objects | C5/C6, T3/T4/T6; separately configured combined tasks. | Callback, lifecycle, aliasing and interleaved-state controls pass; each ABI test uses a frozen original endpoint. |
| P6: advanced builds | L6 and T7, then carefully selected additional compiler/build conditions. | Optimized-location equivalences and compiler/runtime-specific ABI probes are independently validated. |
| P7: measurement release | Freeze task selection, budgets, identities, scoring and report definitions before new runs. | Offline cache-only execution makes zero provider calls; global reports reproduce; certification gaps are visible. |

The current implementation covers P1/P2 for supported new paths and P3 for
the restricted C profile; P0's future
capability contracts and controls remain pending. Unsupported budget routes fail
before launch; historical execution cannot be certified from today's installation. P3 is a gate for claiming
return provenance in the restricted C track. Native legacy regrading after P1
improves byte comparison but does not satisfy R6.

## Repair specifications

### R1: exact values and versioned grading

Create a shared return-record contract: invocation ID, return status, value kind,
byte length and exact value bytes. Integer-valued tasks retain an explicit integer
contract; string/byte tasks compare bytes without newline or encoding normalization.
Keep candidate stdout/stderr separate from return records. Protocol validity and
behavioral correctness are separate fields.

Numbered hexadecimal transport can be an interim native wrapper implementation.
Native-process framing remains unauthenticated. R6 authenticates only its
host-controlled execution profile, not the legacy native wrappers. The comparison layer must also
accept host-generated records, so the executor can change without changing semantic
oracles. Do not force invalid UTF-8 through a language interface that requires a
valid UTF-8 string; exercise arbitrary bytes through the byte-buffer contract.

Required controls: appended CR; embedded LF and CRLF without shifting subsequent
IDs; empty values; embedded NUL; non-UTF-8 bytes where permitted; duplicate, missing,
extra, malformed and out-of-order records; stdout noise; abnormal termination;
per-call length/status mismatch. Define whether ordering is required independently
of ID uniqueness. A complete, correct batch is the positive control.

Apply the shared contract to basic, complex, STL and core routes in
[run.py](../benchmarks/scripts/run.py). Keep old grades. Regrade unchanged saved
candidate bytes into new records with grader/vector/driver/compiler/executor
identities. Report old/new differences, including changed failure categories and
point totals. An unsuccessful regrade never removes a historical grade.

### R2: Go imports

Use a small trusted Go helper based on the standard-library
[parser](https://pkg.go.dev/go/parser) and
[strconv.Unquote](https://pkg.go.dev/strconv#Unquote). Parse actual import specs and
decode their literals; apply the restricted package policy to decoded paths.
Compile/cache the helper as a grading dependency, not a per-model tool.

Test ordinary imports, comments and strings containing restricted words, aliases,
blank/dot imports, grouped imports, raw and escaped literals, and malformed source.
Record parse error, import-policy rejection and compilation error separately.
Apply the same policy to both current grader paths and future multi-file candidate
artifacts. Import inspection alone is not an execution sandbox.

### R3: certify final requests

Both adapters must validate a common request contract **after** provider-specific
serialization and **before** the request is forwarded. Record cap units and scope:
per-response generation cap, total wall time, tool-call count, tool timeout, retry
count and reasoning setting. An API's reasoning/output token accounting must be
documented per route; unlike quantities are not silently treated as equal budgets.

Capture requests against a local fake provider using dummy credentials. Cover
missing/wrong/oversized caps, unsupported routes, tool continuation requests,
retries and streaming paths. Invalid requests must produce zero upstream forwards.
Keep ZCode's existing pre-forward checks and expand their coverage. Replace DSH
metadata-only assurance with transport-level assurance.

If the selected route cannot enforce the requested cap, fail the certified
condition before a billable request. A separately named uncertified condition may
exist only when explicitly configured. Do not silently switch provider or claim a
post-generation client truncation enforced a server generation cap.

### R4: effective identities

Implemented for new inference through the [runtime/provider attestor](runtime-attestation.md)
and the existing dispatcher. Provider resolution precedes hashing; effective
gateway/harness settings and reviewed installed components are bound and checked
before/after execution. Old observations stay in their unattested namespace.
Candidate grading uses content-addressed compiler/helper/linker/header/library/
loader/Python/executor images, strict archive admission and pre/post drift checks.
Workers load copied declared source bytes, excluding host project bytecode caches.
Image-admission and execution code are semantic dependencies; archive construction
and compression are separate storage work. Kernel and CPU capabilities enter new
grading identities. See [closed grading runtimes](grading-runtimes.md).
Historical missing execution facts cannot be recovered from current installations.

Resolve the effective provider and credential *mode* before computing task identity;
do not include credential values or account tokens. Freeze that resolution for
execution. Bind resolved component content and effective nonsecret configuration:
REA package/loaded dependencies and wrapper, Ghidra distribution/custom scripts,
Node/Java, DSH or ZCode runtime and adapters, tool gateway, tool policy/catalog,
environment overrides, model route and budget contract. Use version labels together
with verified content digests; define and test the dependency closure, including
symlink targets and mutable configuration.

Update adapter profile verification as well as the profile producer: current DSH
verification only accepts profile file paths under `benchmarks/`. Represent runtime
components with reviewed logical IDs and resolved digests instead of broadening
that check to arbitrary paths. Detect changes between identity resolution and
execution; refuse changed components rather than running under a stale identity.

Three independent identities are required:

| Identity | Inputs | Changes that must not invalidate it |
| --- | --- | --- |
| Inference | Exact public task package, binary, tools/policy, effective model/provider/settings, inference runtime, budgets | Grader/vector changes, prices, report styles |
| Grading | Candidate artifact hashes, invocation contract, oracle/vectors, grader/driver, candidate compiler and executor identity | Report styles, price updates |
| Report/accounting | Chosen grading IDs, ledger/accounting rules, pricing source/date, review rubric and report code | No mutation of inference or grading receipts |

Changing target compiler/flags changes the binary/build condition. Changing the
candidate compiler changes grading identity. Distinguish both from the analysis
runtime. Preserve legacy identities and label missing runtime/provider evidence
as unknown; a migration binding must never assert an unobserved historical digest.

### R5: request usage ledger

Normalize each request attempt to a stable ID with provider route, timestamps,
outcome, usage source/semantics, known input/output/cache token components and
`complete`, `partial` or `unknown` status. Preserve explicit incomplete evidence.
Deduplicate cumulative streaming observations and reconnect/retry attempts.

A process exiting normally does not establish usage completeness; a failed task
can have a complete ledger. Zero requests and zero observations do not establish
a measured zero bill. Distinguish confirmed no-forward execution from missing
request evidence. Keep known subtotal, unknown components, estimated cost range
and billing provenance separate. Codex subscription estimates are not provider
invoice amounts. Store pricing revisions without rewriting token evidence.

Required controls: missing one request's usage, explicit false on normal exit,
complete ledger on task failure, repeated cumulative SSE usage, retries with
separate charges, zero-evidence receipts, cache-token accounting, absent prices.
Regenerate accounting from saved evidence without running candidates or models.

### R6: host-controlled returns

Printing numbered frames, a secret marker or a hidden file descriptor in the same
native process cannot establish that a record came from a genuine function return.
Keep this problem separate from byte framing and filesystem isolation.

The restricted freestanding C17 x86-64 SysV execution profile is implemented
under a host-managed instruction emulator. Its pinned dependency is
[Unicorn](https://github.com/unicorn-engine/unicorn); its
[documented API](https://www.unicorn-engine.org/docs/tutorial.html) provides memory,
register and bounded execution controls. Engine suitability, the static ELF loader and the supported
call boundary have 30 offline controls. See [the execution contract](c-executor.md);
this is a restricted prototype, not general native execution or a verified emulator.

The host owns invocation IDs, input snapshots, allowed memory regions, instruction
budget and records. Guest code has no host syscalls or access to the host record
store. The host reads return registers and permitted memory effects after a valid
return to a controlled call boundary. Define supported relocations, helpers,
alignment, stack discipline and termination statuses. Isolate the emulator worker
as well; the emulator and loader remain trusted components.

Required negative controls include forged stdout frames followed by exit, traps,
out-of-bounds writes, caller-state corruption and exhausted execution budgets.
Valid returns and allowed overlapping-buffer effects must pass. General C++
exceptions, Rust and Go runtimes remain outside this prototype's support claim.
Their current native runs retain an explicit provenance limitation.

## Capability definitions: three families, nineteen sub-capabilities

Each task declares source features, structures actually observable after compilation,
and behaviors/artifacts that scoring checks. Using `std::map` in the source does not
prove a candidate recovered `std::map`; an equivalent implementation can be correct.

Each row requires a correct control, a structurally different equivalent control
where applicable, and a single-defect negative control. All task sessions are fresh.
Standalone localization, C reconstruction and type/ABI tasks must not consume each
other's answers. Combined tasks are distinct conditions with stage-level scores.

### L: localization without business entry hints

Artifact: `locations.json`. Use module-relative addresses/RVAs with binary SHA,
target status (`present`, `absent`, `unanswered`), candidate roles, bounded ranges
and relations. Accept validated alternative locations and optimized equivalents.
Cap submitted candidates/ranges/graph size so listing the whole program cannot win.

| ID | Measured sub-capability | Verifiable artifact and control | Stage |
| --- | --- | --- | --- |
| L1 | Business-function root | Primary top-1 root; up to three alternatives are diagnostic. Include a similar distractor and an absent-target binary. | First |
| L2 | Semantic decision/update/error sites | Bounded instruction ranges for named roles; precision and coverage with an over-report penalty. Negative control selects the neighboring wrong branch. | First |
| L3 | Dispatch and indirect calls | Condition/dispatch-site/target tuples checked against private mappings; wrong-condition edges fail. | First |
| L4 | Data-to-code relations | Object or byte-range references with read/write role and code location; wrong direction or unrelated string fails. | First |
| L5 | Cross-function reasoning chain | Root, bounded nodes and directional edges; missing required edges and unrelated appended nodes are measured separately. | First |
| L6 | Optimized instances | Inline instances, clones, cold regions, thunks and LTO transformations using validated equivalence sets; initially diagnostic. | Advanced |

Build a public-manifest projection in task preparation. Removing entry hints only
from the prompt is insufficient: the current runner copies the whole manifest.
Allowlist public fields and reject entry addresses, handler mappings, original
business symbols, private types/layouts and labels anywhere in the task package.
ELF loader entry, runtime metadata, strings and other clues intrinsic to the
distributed binary are documented conditions, not automatically forbidden hints.

Private truth comes from the same build's symbols/DWARF/link and optimization maps,
then is validated against final stripped bytes. Inlined targets are not absent
targets. Absence controls use binaries in which the requested behavior is actually
absent. Address normalization must work for PIE and non-PIE builds.

### C: independent C reconstruction

Artifact: C17 `solution.c` plus explicitly allowed headers. Provide a known target
location so initial C results do not also measure localization. Use the same binary
and semantic contract as corresponding original-language tasks, with independent
attempts. Add a full-semantics code-generation control to help distinguish language
generation failures from reverse-engineering failures.

| ID | Measured sub-capability | Verifiable artifact and control | Stage |
| --- | --- | --- | --- |
| C1 | Numeric and bit semantics | Width, wrapping, shifts, signed boundaries and endianness; wrong-width/sign controls. | First |
| C2 | Control flow and algorithms | Branches, loops, ordering and duplicate/tie behavior; alternate algorithm passes, one wrong boundary fails. | First |
| C3 | Parsing and serialization | Exact output bytes, length and status; CR, embedded NUL, malformed and truncated inputs. | First |
| C4 | Memory effects and aliasing | Return plus permitted writes and preserved regions; overlap and disjoint-buffer cases, guard/canary corruption controls. | First, after R6 gate |
| C5 | Callbacks, errors and lifecycle | Callback arguments/order/count, failure effects and create/destroy behavior; wrong error cleanup or callback signature fails. | Later |
| C6 | Persistent state | Init/step/reset/destroy sequences over interleaved instances; stale/global cross-instance state fails. | Later |

Initial byte-buffer interface proposal:

```c
int32_t reconstructed(const uint8_t *input, size_t input_len,
                      uint8_t *output, size_t output_capacity,
                      size_t *output_len);
```

Specify allowed lengths, nullability, overlap, integer semantics, error writes,
output-capacity behavior and resources. C4 may require additional declared regions;
C5/C6 require explicit state/callback interfaces rather than pretending this one
function captures them. Avoid undefined behavior in the normal task domain. A
fixed-machine undefined-behavior study must be a separate condition.

The candidate execution environment receives only that invocation's permitted
inputs. It cannot read the original binary, reference executable, hidden vectors,
answer files or a target execution service. Models still analyze the distributed
target statically through the configured tools. Dependency/embedded-binary checks
provide limited evidence; they are not proof that a candidate contains no interpreter.

### T: types, layout and ABI artifacts

Artifacts: `types.json` and `abi.json`. Validate their schema, then generate probes
or bridges **from the submitted description**. Text descriptions or compilation
against two mutually consistent but incorrect declarations are insufficient.

| ID | Measured sub-capability | Verifiable artifact and control | Stage |
| --- | --- | --- | --- |
| T1 | Scalar representation | Width/kind and signedness where identifiable; alternate indistinguishable signedness is not uniquely graded. | First |
| T2 | Aggregate layout | Size/alignment/field offsets/nesting/array stride; a one-byte offset or packing defect fails observable probes. | First |
| T3 | Pointer and object roles | Object/buffer/length/capacity/alias descriptions drive access probes; swapped length/capacity or incorrect aliasing fails. | Later |
| T4 | Variants and subobjects | Discriminants, payloads, unions and object substructures; wrong active alternative or base offset fails. | Later |
| T5 | Call boundary ABI | Argument/return registers, stack, hidden return pointer, preserved registers and alignment; independent wrong-convention controls. | First |
| T6 | Indirect and object calls | Function pointers, callbacks, `this`, vtables and adjustment thunks; wrong slot/adjustment fails. | Later |
| T7 | Rust native / Go internal ABI | Fixed compiler/runtime/artifact-specific probes, kept separate from stable C FFI results. | Advanced |

Use frozen original caller → generated candidate replacement, and generated bridge
→ frozen original callee. Every measured direction retains an original compiled
endpoint. Compare defined bytes and observable fields, not unspecified padding.
Unobservable/erased facts are excluded from the strict score; equivalent
interpretations are accepted and missing facts remain missing.

Start with Linux x86-64 SysV, selected `ms_abi` call boundaries in ELF, and Rust
`repr(C)`/`extern "C"`. An ELF `ms_abi` test does not establish Windows PE/MSVC
object-layout or exception compatibility. Rust object layout and calling ABI are
separate conditions; C FFI success is not Rust-native or Go-ABIInternal success.
Prevent inlining/LTO from removing a boundary under test; optimized-away boundaries
belong to a separately defined task.

## Program families and controlled builds

Start with six reusable families, not one new suite for each model or audit turn:

| Family | Shared behaviors | Useful capability combinations |
| --- | --- | --- |
| Record codec | Tags, parsing, byte/string bounds, aggregate records | L2/L4, C1/C3/C4, T1/T2 |
| Dispatch registry | Tables, containers, function pointers, indirect selection | L1/L3/L5, C2/C5, T5/T6 |
| Numeric aggregation | Mixed widths, sorting/ties, reductions and container iteration | L2, C1/C2, T1 |
| Variable objects | Nested layouts, tagged variants, variable-length storage | L4, C3/C4, T2/T3/T4 |
| Callback policy | Callbacks, error propagation and lifecycle | L3/L5, C5, T5/T6 |
| Session state | Multiple requests, reset and interleaved instances | L5, C6, T3/T4 |

Keep sources human-readable and idiomatic. Share private truth across derived
tasks, but do not leak a previous task's artifact into another session. Existing
published families are regression material; a later generalization claim needs
separate held-out program families, not renamed copies.

New baseline: pinned GCC and Clang `-O2`, Rust optimization level 2, and normal
optimized Go builds, with stripped public targets. Keep the user's existing
`-O0/-O2/-O3` compiler matrix as separately identified **build-only** points until
explicitly registered for inference. Preserve historical compiler/build settings.
No assumption that a higher optimization level is always harder is built into scoring.

Record target compiler/version/flags/standard/library/ABI/link/architecture separately
from candidate compiler settings. Private debug and public stripped artifacts come
from the same build; verify that relevant loaded code/data stay identical after
stripping. Validate answer locations after stripping. Introduce PIE, linking, ABI
and architecture variations as paired changes before expanding a Cartesian matrix.
Clang 20 features are explicit build points, not implicit changes to older cases.

## Repository changes and data model

Retain the current structure and entrypoints. Shared machinery belongs with shared
scripts; family sources/oracles/build data belong under registered suites. Proposed
locations below describe permanent responsibilities, not files already created:

```text
Makefile                         existing command surface
benchmarks/config/               model/case/build/capability selections and schema revisions
benchmarks/scripts/run.py        one plan/run/regrade coordinator
benchmarks/scripts/cache.py      independent inference and grading cache validation
benchmarks/scripts/grading/      shared return protocol, Go helper, executor and artifact validators
benchmarks/scripts/              request/usage/identity utilities and offline checks
benchmarks/harnesses/            request-contract enforcement for each harness
benchmarks/suites/<suite>/       family sources, builds, public packages and private truth
benchmarks/runs/<experiment>/    immutable inference attempts plus versioned derived records
benchmarks/public/               allowlisted aggregate snapshot only
benchmarks/reports/              generated private global report (ignored)
benchmarks/site/                 generated public preview (ignored)
docs/                           generated public HTML for master /docs Pages
guides/                         authored specifications and operating documentation
```

Do not restore the retired top-level `benchmarks/grading` or add dated runner/report
scripts. Update existing strict configuration validation and the suite registry.
The current cache assumes a language source file and one selected grade; it must
support typed artifact sets without routing all artifact types through code extraction.

Task hierarchy: family → build → task/condition → attempt. Required identity fields:
`family_id`, `build_id`, `task_id`, `capability_id`, `source_language`,
`output_language` when applicable, artifact schema/version, hint/tool profiles,
binary and public-package hashes, effective provider/model/budget/environment,
attempt ID, inference ID and grading ID. A localization/type task has structured
artifacts rather than an invented output language. New capability definitions stay
disabled until their gates pass; selecting `all` means all enabled configured tasks.

## Migration, caching and commands

Do not rewrite `result.json`, original grades, saved candidates, event/wire traces,
legacy completion receipts or their preservation manifests. New grading records
should live under an attempt's `grades/<grader-version>/<grading-id>/`; derived
usage/accounting records should be versioned separately. Select a preferred grade
through an explicit index/policy, not whichever filename happens to exist last.
Raw records remain local. Maintain old/new comparison views and artifact hashes.

Split validation outcomes into valid inference, grading pending, invalid inference,
and historical identity not certified. A grader update creates pending offline work;
it must not trigger a new inference attempt. Historical runtime uncertainty is a
comparison limitation, not permission to relabel the old cache as newly certified.
New effective inference inputs require a distinct cache key and preserve the old
attempt. Existing reviewed compatibility bindings remain narrow and auditable.

Keep these implemented commands and their established roles:

```bash
make benchmark-plan                 # configuration/cache preview; no model calls
make benchmark BENCHMARK_FLAGS=--offline
make reports                        # derived global reports from saved evidence
make pages                          # local sanitized public HTML generation
```

The existing offline benchmark command refuses missing inference and can complete
pending versioned grading for valid cached inference. Implemented additions use the
existing coordinator and Makefile:

```bash
make check-measurement              # returns/imports/usage/pipe limits/grading-store controls
make regrade                       # always offline; versioned grades/accounting, never inference
```

`make benchmark` remains the single configured run command for native and enabled
capability tasks: prepare selected new/stale capability inputs, reuse valid inference,
complete pending grading, run eligible missing inference, then generate global
reports. Its offline mode refuses all missing inference before credentials or a
provider can be used. Regrading, checking, reporting and planning never authenticate
or launch a provider. Run-lock/stop-state protection and duplicate prevention remain.
`make prepare-capabilities` remains an optional explicit offline preparation/control
command using the same action cache. Read-only plans represent pending and invalid
cells without compiling or guessing an attempt path. Stop-state guards still
require explicit resume or offline mode before the run begins preparation.
Actual request-forward counts, not just coordinator launch counts, must verify zero
provider calls in the offline acceptance suite.

## Global report and release acceptance

Extend the existing global report rather than creating a report for each work
package. Show capability vectors and family-level macro averages, alongside
per-task behavior checks. Compiler/optimization variants are paired conditions,
not extra independent program families. Cached reuse is not another sample.
Avoid a single overall ranking until weighting and coverage have been defined.

Publish a predeclared scheduled-task denominator and separate completion,
behavioral correctness, artifact validity, readability, token usage and cost.
Infrastructure-invalid, unstarted and unsupported tasks remain distinguishable.
Do not quietly remove failed attempts or score unrun tasks as model-correctness
observations. State readability's rubric, review provenance and uncertainty; it
does not establish recovery of original names or container choices.

Add old/new grade deltas, budget/runtime certification, return-provenance level,
usage completeness, known cost subtotal/unknown remainder, and grading/accounting
version summaries. Historical uncertainty must survive regeneration. Do not
retroactively certify missing generation limits or runtime identities.

Extend [public report validation](../benchmarks/scripts/check_public_report.py)
and its strict allowlist for approved summary fields. Publish only derived HTML,
CSS/JS and necessary static assets to `docs/`; no credentials, local paths, raw
reasoning, provider JSON, candidate traces or private truth. Continue local Pages
deployment from `master/docs` without a custom Actions workflow.

Before enabling a new inference condition, verify all of the following:

1. Relevant positive/equivalent/negative controls and leak checks pass.
2. Budget and runtime/provider identity are certified or explicitly configured as
   an uncertified condition; unsupported conditions fail before forwarding.
3. Sandbox resource checks cover CPU, wall time, memory and **captured pipe output**;
   file-size limits alone do not bound stdout/stderr held by the parent.
4. Existing inference artifacts retain their recorded hashes; repeat offline
   regrading produces the same semantic results and zero provider forwards.
5. Compiler/build points, scoring, family weights and scheduled denominators are
   frozen for the measurement; sessions cannot read another condition's outputs.
6. Global report values match selected versioned records, rebuild reproducibly,
   and pass public allowlist/canary/link checks.

`make check-capabilities` verifies localization preparation offline. The added
`make check-structured-grading`, `make check-buffer-capabilities`,
`make check-cpp-buffer`, `make check-abi-probes` and
`make check-capability-coordinator` verify the separate typed/C/C++/ABI paths. They do not
certify C5/C6, later T conditions or the full capability release. The implementation
provides the restricted C executor but does not enable new capability tasks or rerun model inference.
`make check-capability-registration` verifies pre-dispatch schedules, complete
execution bindings, single-use dispatch intents and historical provenance;
`make check-capability-measurements` verifies the read-only global collector and
family/lineage score definitions. These controls do not satisfy the missing
task classes, independent validity review or premises-only model ablations.
Global HTML can change through offline regrading; original grades remain available.
`make check-lifecycle-capabilities` separately verifies the explicit C5/C6
preparation path. Its 22 controls include equivalent opaque layouts, callback
argument/count and rollback defects, destroyed/reset/interleaved state, memory
guards, source-bound admission, distinct component scores and cached reuse.
They do not certify coordinator enrollment, independent validity or release.
