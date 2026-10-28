# Type, layout and ABI preparation

The permanent capability suite has separate T1, T2, T3, T4, T5 and T6 conditions.
Real-model diagnostics were authorized on 2026-10-23; all capability claims remain
unreleased pending their review and measurement gates. These are
bounded integer-record, pointer-object, tagged-variant and indirect-call interoperability tests, not a completed type-recovery
benchmark or general native-runtime support.

## Configuration and commands

`benchmarks/config/capabilities.json` selects family/ABI/profile combinations.
`benchmarks/config/abi-probes.json` freezes the trusted compiler flags, supported
families and resource bounds. The existing coordinator prepares, runs, caches,
regrades and reports these tasks; there is no model-specific runner.

```bash
make setup-c-executor setup-capabilities
make check-abi-probes
make prepare-capabilities
make check-capability-coordinator
make reports
```

The integer-record scope contains eighteen baseline definitions: three existing
families, two calling conventions and three isolated recovery conditions. Twelve
additional scalar/layout counterfactuals retain the nonmeasured public premises.
The 35 ABI controls include 216 native/emulator reference comparisons across
baseline and counterfactual targets. Current coordinator and preparation status
comes from the global report and source-bound receipts, not an older passing
count. Conditions and repeated probes are not independent program families or
completed model measurements.

| Condition | Supplied premise | Submitted artifact | Scored capability |
| --- | --- | --- | --- |
| `scalar_recover` | Record offsets, sizes, alignment, array count/stride and ABI | `types.json` | T1 integer widths and observable signedness |
| `layout_recover` | Semantic field roles, scalar types and ABI | `types.json` | T2 observable layout, nesting and stride |
| `abi_recover` | Complete record type and known callee/caller locations | `abi.json` | T5 inner call/return convention and machine locations |

The targets are GCC 13.3/C++20 `-O2` static ELF64 AMD64 builds. They use trivial
8-, 16- and 24-byte records, including nested arrays. Both SysV AMD64 and GCC
`ms_abi` boundaries are exercised. The outer host probe remains SysV. An ELF
`ms_abi` result is not a Windows PE/MSVC layout or exception-compatibility score.
The separate [T3 pointer/object condition](pointer-conditions.md) pairs physical
layouts with one-level and two-level pointers. Its artifact drives original-endpoint access
probes; it does not share the record-graph schema below. The global report keeps
all conditions and their source-bound control status together. The
[T4 tagged-variant/subobject condition](variant-object-conditions.md) adds two
layouts; its acceptance controls are tracked
separately from the integer-record and pointer gates.
The [T6 indirect/object-call condition](indirect-call-conditions.md) adds two
paired originals under a complete supplied root layout and known call signatures.
It measures receiver/callback/context relationships, actual virtual slots and
signed receiver adjustments against compiler-generated thunks. Those two
definitions have a separate 28-control probe gate.
Two configured [T7 native-language ABI conditions](native-language-abi.md) add
pinned Rust/Go originals, known aggregate layouts and separate physical-transfer
artifacts with a separate 40-control preparation gate. L6 and matched Clang/Rust/Go
reconstruction have their own controls. Full coordinator acceptance and
measurement release remain separate gates. Stable C FFI results never count as
native-ABI recovery.
The record variants share an interface/probe template and carry the conservative
`integer_record_interfaces_v1` lineage. They are related conditions, not three
new independent program families for a future macro average.

## Counterfactual task validity

The `scalar_recover_counterfactual` and `layout_recover_counterfactual` profiles
each select all three record families and both calling conventions. They use the
ordinary task coordinator and the same T1/T2 scoring conditions. Private variant
IDs stay outside the public prompt, manifest and tool catalog.

Scalar counterfactuals change observable width or signedness while preserving
every supplied offset, extent, alignment and array fact. Layout counterfactuals
reorder root and nested fields while preserving supplied scalar types. Within
each pair, the prompt and tools are byte-identical, and the supplied facts,
compiler projection, abstract calling convention and artifact contract agree.
Binary identity and endpoint addresses remain intrinsic to the selected target.

The compiler receives one selected authored header through a declared logical
source alias. Its action binds that header and alias, excluding unrelated
headers and private gold-control facts. All baseline original-build action
inputs are retained. The catalog at
`benchmarks/suites/capabilities-v1/config/abi-variants.json` selects the source and
reviewed control facts; it never substitutes those facts for original execution.

Positive controls pass the original caller and callee in both directions.
Equivalent JSON ordering is accepted. Fixed-description controls rebind only a
description's opaque binary identity: the baseline description is tried against
the counterfactual, and vice versa. They check whether these simple blind guesses
can pass the paired targets with no newly recovered type facts.

These are deterministic program controls, not LLM no-binary performance or an
exhaustive proof that no equivalent description exists. A separate premises-only
model ablation and the remaining task-validity review are still required before
claiming a reverse-engineering capability measurement. No model experiment is
enabled or charged by this preparation.

The two compiler attributes follow the [GCC reference](https://gcc.gnu.org/onlinedocs/gcc/x86-Function-Attributes.html).
The Microsoft-style preservation/home-area contract follows the
[official x64 convention reference](https://learn.microsoft.com/en-us/cpp/build/x64-calling-convention?view=msvc-170).
The artifact's preservation set covers GPRs and low-128-bit XMM registers,
including RSP through return-stack discipline. MXCSR/x87 control state and upper
AVX lanes are outside this initial schema.

## Independent original endpoints

The builder compiles the authored original caller and callee once into
`original.o`. It links a default replacement to produce a private debug ELF and
an otherwise identical stripped public ELF. Every load mapping and allocated
section must agree after stripping; private symbols/debug data are absent from
the public copy. The object hash and compiler/binutils images enter build identity.

The actual model submission is parsed and used to generate probes inside the
pinned C++ compiler/Python image. No model code is accepted in T conditions.
Generated code is linked with the **unchanged frozen original object** in both
directions:

1. The original caller invokes a generated replacement using its original
   declaration. Original return-slot guards detect an incompatible size or write.
2. A generated bridge calls the original callee using the submitted description.
   The host compares its defined field values with a separately executed frozen
   original reference.

The candidate description therefore cannot pass simply by declaring the same
wrong layout on both sides. Type facts determine generated record fields,
padding, array cells and accessors. ABI register/stack locations directly
determine assembly transfers, hidden return-pointer handling and preservation.
Consistency checks additionally enforce the supported convention contract.
Original alignment/home-slot and preserved-register witnesses make those
constraints observable in the instrumented build. Fault-injection controls
deliberately disable the consistency check to test the machine probes themselves.

Calls execute under the existing host-controlled instruction emulator. The host
owns return records and observations. A wrong declaration can fault or corrupt
an original guard; these outcomes fail the interface task. Such negative
interoperability probes describe fixed machine execution, not valid ISO C++
semantics for mismatched declarations. General C++/Rust/Go model submissions keep
their existing native-process provenance limitation.

Acceptance also calibrates the original reference independently in the pinned
native C++ runtime: six original builds times twelve nonce vectors, or seventy-two
calls. The native runner links only the frozen original/helper objects and a
trusted CLI; it never consumes a submitted description or generated probe. Each
native observation must match the emulator's reference observation. The native
calibration has its own cache action and is not rerun on a parent cache hit.

Only defined semantic field values and observable effects are compared.
Unspecified padding is not scored, arbitrary type IDs/field ordering are allowed,
and equivalent extra padding fields/alignment descriptions can pass. Unsupported
types and malformed/cyclic/oversized descriptions fail bounded admission.

## Public boundary and caching

Each model receives exactly `target`, `prompt.txt`, `manifest.json` and
`tools.json`. T1 public layout facts contain no scalar-width/signedness answers
or revealing type IDs. T2 supplies scalar facts without offsets or stride.
T5 supplies the type while withholding the convention and its source flags.
Private object bytes, compiler commands, source, vectors and references stay in
the grading specification. Target compiler metadata is separate from the
generated-probe compiler and emulator identity.

Original builds, description compilation, host invocations and aggregate grades
have separate actions. Probe-only generator changes cannot invalidate original
builds. Public packages and private grading specifications have separate hashes;
vector/grader revisions append grades without asking for inference. Parent-grade
cache hits launch neither the compiler nor emulator. Report/pricing changes are
outside these semantic actions. Old inference, answers and grades are retained.

The global HTML report publishes preparation/control status only until capability
measurements are enabled and their remaining release gates pass. Controls and
cache hits are not model samples, token observations or cost measurements.

## Remaining scientific-validity gates

The integer-record scope validates the mechanism, not a released capability
ranking. T2 scalar facts are ordered by semantic path, independently of physical
field order. Paired scalar/layout originals now keep the nonmeasured supplied
premises fixed. Controls 27–35 check those premises, both original endpoints,
equivalent descriptions, wrong selectors and selected-source cache identity.
Fixed baseline descriptions fail on counterfactuals and fixed counterfactual
descriptions fail on baselines. These deterministic controls do not establish
that a model must inspect the binary to solve the task.

Before release, the remaining review must:

1. Bind its findings to the exact public binary, original-object/build identity,
   private contract and grading version. Review each measured field against an
   observable original-endpoint effect in both directions. Accept indistinguishable
   interpretations; exclude erased ownership, unspecified padding and unused size.
2. Check correct, structurally equivalent and single-defect artifacts against
   the same original endpoints. Review false positives and false negatives, not
   only whether the gold description passes. Record reviewer identity, evidence,
   unresolved alternatives and resolutions. The coordinating agent's own controls
   are not an independent review sign-off.
3. Run separately registered premises-only model ablations in fresh sessions,
   withholding target bytes, analysis-tool access to those bytes and previous
   answers. Preserve the supplied nonmeasured facts and artifact-binding identity.
   Compare paired targets under the same declared budgets and keep these ablations
   separate from recovery observations. Real-model execution is now authorized;
   the separate condition is implemented and registered through the normal coordinator.
   Its real-model observations remain pending; an implemented condition does not
   establish the ablation result.

Keep the shared template lineage in denominators and family macro averages.
Broader T1 scalar kinds and floating-point classifications require separately
declared contracts and controls before making those additional coverage claims.


## Effective analysis context in independent review

Record the analysis provider, runtime identity and effective seed policy alongside
the public-package and binary digests. The opt-in `public-given-entrypoints-v1`
condition derives anonymous function starts only from the public ELF mappings and
entry RVAs already supplied in the task manifest. It supplies no recovered names,
types, layouts or signatures. Its exact seed bytes are inference Merkle inputs.
The four-file frozen public packages and the existing review archive are unchanged.

The offline gate checks derivation for all 144 known-entry packages and confirms
that the nine localization packages remain unseeded. Actual MCP/Ghidra function
access has been verified for the baseline record-codec scalar caller; that single
query does not certify access to every function in every task. Reviewers should
record unavailable entries as tool/environment defects, rather than inferred
model failures, and check the entries needed by each reviewed task.

Premises-only sessions retain the corresponding public-address clarification but
physically withhold target bytes, expose no analysis tools and set no seed-file
override. They use separate registrations and fresh sessions. Keep their outcomes,
seeded recovery outcomes and historical default-analysis attempts separate. The
user-arranged independent review is still pending.


## Serialization and executed evidence

For the opt-in `.role-string-v1` conditions, review the additional delivered
record-role notice alongside the unchanged public archive. Each `role` is one
identifier string; a semantic `path` array is a different value. This overlay
changes prompt conditions without disclosing measured type facts. The archived
base packages alone do not describe every later delivery condition.

Artifact admission, scalar/layout facts and original-endpoint execution are
separate findings. A strict zero caused by invalid serialization is not evidence
that any semantic machine checks ran. Invalid artifacts with no checks are
reported as not executed even if an older raw grade used a nominal host-return
label. Old grade files and numeric scores remain unchanged. Any edited-format
sensitivity result must identify the edit and stay separate from the model score.
