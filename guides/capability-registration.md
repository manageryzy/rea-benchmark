# Immutable capability measurement registration

The existing `make benchmark` entrypoint registers prospective capability work
under its batch lock. No separate runner or dated configuration is required.
The user authorized a bounded diagnostic pilot on 2026-10-23. Capability
observations remain provisional until the task-validity and release gates in
[the implementation plan](audit-implementation-plan.md) are satisfied. The
pilot selection is documented in [capability preparation](capability-preparation.md).

`make check-capability-registration` runs the 38 offline admission/report
controls. Provider resolution, runtime inventory and launch callbacks are local
fixtures; socket connections and real model dispatch are forbidden. The
source-bound receipt is a prerequisite for new capability inference.

## Before a model queue starts

1. Publish a content-addressed schedule under
   `benchmarks/runs/capabilities-v1/registrations/`. It freezes the selected
   capability denominator, prior/prospective states, semantic definitions,
   model settings, limits, metric policy, family/lineage weights and parallelism.
   It is written before automatic preparation begins.
2. Prepare selected pending targets through the ordinary preparation cache.
   Recheck the frozen selection and refuse changed previously prepared targets.
3. Check each prospective task's validation gates and serialized request budget.
   Resolve every participant's provider route before runtime inventory. Bind
   all participants' runtime manifests before any queue can dispatch.
4. Publish a complete execution binding. Every selected cell has its verified
   original build, binary and public package identity. Prospective cells also
   have the exact inference identity, attestation and candidate-independent
   grading inputs/platform/version. Recheck scoring and semantic configuration
   before publishing the binding.
5. Verify each task against the binding, then create an exclusive, single-use
   dispatch intent before the provider callback. Recheck before live grading.
   Cancellation after binding prevents dispatch and releases the batch lock.

Missing participants, unsupported caps, changed settings or scoring, corrupted
records and reused dispatch intents fail admission. Records preserve their first
creation metadata and are never overwritten with a new timestamp. Offline
control records cannot authorize a real provider. Local records are operational
admission evidence; they are not an external timestamp service or model-weight
signature.

## Cache and historical provenance

Registration is outside inference and grading action identities. A valid cache
hit creates no new registration or provider dispatch. Validation against a
current attested action can still resolve its route and inventory local runtime
components; drift must reject that reuse. The read-only report consumes saved
evidence without those probes. Prior observations retain their actual provenance
in a mixed schedule. Offline runs create no registrations, and missing historical
registration remains unknown.
Changes to labels, prices, report frequency or report presentation do not change
the semantic registration identity. Admission/measurement implementation changes
require a new registration while preserving unrelated cached inference.

The report distinguishes original registered scoring from a later offline
regrade. A compatible new grade may preserve inference registration; changing
its scoring contract is explicitly shown as a grading revision. Neither cache
reuse nor a revised grade creates another observation or model charge.

## Global and public reports

`make reports` verifies saved registration records and original build metadata
without providers, compilers, candidate execution or installed-runtime probes.
It regenerates `capabilities.html` alongside the existing global report. The
private report contains a registration summary JSON; the public HTML contains
only the allowlisted projection.

Build projections show the saved original compiler/version/standard/optimization,
library, architecture, linkage, stripping and disclosed ABI. Full private
commands, presence/convention macros, linker symbols, source paths and measured
ABI answers are excluded. Candidate compilation retains a separate grading
identity. Legacy public snapshots migrate to a missing-registration state,
without invented schedules, denominators or observations.
The allowlisted snapshot retains safe flags and full build/compiler/binutils
identities. Each HTML build row now has an expandable “Flags and full identities”
cell with selectable complete hashes. Private compiler commands, their digests,
presence macros and measured convention labels remain excluded.

Registration and reporting complete part of P7. They do not complete independent
task review, premises-only model ablations, broader task conditions,
compiler/language expansion or measurement release. All 19 planned capability
release gates remain open.

The advanced diagnostic conditions bind their own saved original actions: GCC
O3 and LTO, Rust-native and Go ABIInternal. Rust's explicit optimization and
Go's compiler-default optimization are separate labels; no synthetic `-O2`
flag is assigned to Go. The public projection includes safe flags from saved
commands without physical ABI answers. Both original build identity and the
candidate-independent grading platform are sealed before any provider dispatch.

## Evidence-access controls

`--evidence-access premises_only` freezes a separate evidence condition in the
selection, task namespace and dispatch signature. It requires an explicit set
of existing type/ABI recovery cases. Their original public manifest and grading
contract remain intact; actual target bytes are empty and the tool catalog has
no entries. Collection checks the materialized input hashes before classifying
an observation. Controls appear in history and cost accounting but never enter
binary-recovery cells or capability macros.

The optional declared-entry policy is part of the dispatch signature and effective
runtime configuration. Its offline source gate is checked before provider
resolution, and its analysis component digests are checked against captured
participant runtimes before the execution binding is published. A later policy
label change cannot authorize a previously registered task. Seed initialization
adds no new benchmark family or scoring contract.


The optional record-role notice is a separate prompt condition, identified by
`artifact_format_policy` in the task signature and the explicit flag in the
frozen harness configuration. Active inventory and historical observations use
their actual format condition; flags alone cannot relabel an existing action.
The corresponding prompt bytes and effective-configuration Merkle input must
match. A format-edited diagnostic cannot become an original model observation.
