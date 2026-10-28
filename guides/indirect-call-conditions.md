# Indirect and object calls: T6

Status: the original programs, strict artifact parser and closed compiler/executor
path pass all 28 source/runtime-bound controls. Two definitions are enrolled in
the 75-case inventory; the expanded coordinator validation passes all 55 controls.
Model inference remains disabled.
No development result is a model measurement or a released capability score.

## Reproduce the controls

```sh
make check-indirect-calls
```

The source/runtime-bound suite has 28 controls. The successful receipt is
`benchmarks/config/indirect-call-validation.json`; absent, failed or stale controls
refuse production grading. Original compilation, submitted-description compilation,
native calibration, emulator observations and aggregate grading use the existing
Merkle AC/CAS machinery. Valid actions are reused without model calls.

## Measured relationships and supplied premises

The root record layout and all call signatures are complete supplied facts.
Receiver contents and callback-context contents remain opaque. Two operations
each select one receiver pointer, one callback pointer and one callback context
from separate two-element arrays. Each selection is a permutation. The operation
identity is `nonce & 1`.

| Artifact field | Observable relationship |
| --- | --- |
| `receiver_slot` | Which root receiver field belongs to this operation |
| `callback_slot` | Which function-pointer field consumes the method result |
| `context_slot` | Which opaque context is passed to that callback |
| `virtual_slot` | Method index relative to the supplied vtable address point |
| `this_adjustment` | Signed byte delta from the interface receiver to the supplied implementation endpoint's owner |

The task-scoped `calls.json` version 1 contains `schema_version`,
`binary_sha256` and two `operations`. Each operation supplies its zero/one ID and
the five fields above. Only bounded integers are admitted. Unknown fields,
duplicate operation IDs, duplicate selections within an array, boolean values,
invalid slots and unsupported adjustments are rejected. Submitted source,
identifiers, expressions and arbitrary target addresses are prohibited. Reordering
operation entries has no semantic meaning.

Two readable C++ originals use real virtual methods and multiple inheritance.
The paired form changes the receiver/callback/context relationships, virtual
method declaration order and nonprimary-base position. The same original leaf
implementations and callback semantics remain observable. The pinned target
compiler is GCC/C++20 at `-O2`, with stripped public ELF64 AMD64 inputs, RTTI and
exceptions disabled. This is a bounded Linux compiler condition, not MSVC,
virtual inheritance, Rust-native or Go-internal ABI recovery.

## Actual calls and receiver adjustment

The frozen original callee calls a virtual method on its interface receiver,
then calls the selected function pointer with its selected context. Missing
required receivers, callbacks or contexts return the supplied `-9` status without
changing object/context state. The observer includes both objects and both
contexts, so updating a valid but incorrect receiver or context is observable.

The known implementation endpoint for each operation is supplied as a premise.
For a replacement call with `nonce & 2` clear, the description selects an actual
vtable entry and invokes it with the interface receiver. With that bit set, the
description applies its signed receiver adjustment and calls the known original
implementation. Both paths must match the real original virtual call. Wrong slot
and adjustment claims therefore execute different machine behavior; neither is
accepted merely because its JSON is well formed. The originals retain real
compiler-generated adjustment thunks, checked against their same-build symbols
and instruction bytes.

The generated bridge independently constructs the supplied root using the
claimed pointer/context relationships and calls the frozen original callee.
Canonical receiver/context objects and their actual vtables are initialized by a
trusted original fixture function. Candidates do not supply object internals.
Slot/adjustment errors may pass this direction while failing the replacement
direction; whole-task success still requires every check in both directions.

The 64-byte host-owned observation includes the return bits, both object states,
both context states, callback counters, root preservation and protected-storage
preservation. Actual vptrs, pointer addresses and unspecified padding are never
expected semantic values. Protected bytes are checked against their own pre-call
snapshot. Guest stdout cannot supply the observation or a score.

## Acceptance and remaining gates

Twenty-eight nonces per layout cover both operations and replacement paths,
missing arguments, signed/unsigned bit boundaries and large values. Native
execution, emulation and a separate Python oracle must agree on all 56 original
observations. Correct descriptions must pass 112 bidirectional checks. Negative
controls isolate slots, receiver adjustments, receiver selection, function-pointer
selection and callback-context selection; a single-operation defect must remain
partial rather than receive whole-task success. Cross-layout guesses also fail.

Closed worker generation excludes private gold descriptions and the reference
oracle. Source/policy/runtime drift, substituted objects with recomputed digests,
missing validation, malformed transport and wrong binary IDs fail admission.
Loaded engine, facade and contract modules must match their captured source.
Private-vector changes reuse compiler and execution actions; report/pricing and
unselected-header edits preserve unrelated actions. Cold native calibration is
tested with the candidate generator disabled.

Public/private package preparation and typed reporting are integrated. The
collector and registration/build-provenance suites pass 30 and 28 controls,
respectively. The expanded coordinator acceptance passes all 55 controls. All 75 definitions
are frozen with current preparation inputs, and all 73 previous task records are
unchanged. Independent task
validity, premises-only model ablations, broader call patterns and release remain
open. These deterministic controls do not establish model difficulty or prove
that every plausible blind description fails. No ablation runs while experiments
remain stopped.
