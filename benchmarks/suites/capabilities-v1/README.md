# Capability program families — preparation

This permanent suite supplies six readable C++ program families for the planned
localization, independent C reconstruction and type/ABI conditions. Inference is
disabled. Existing native reconstruction experiments remain separate and retain
their original inputs and results.

The current implementation covers strict `locations.json` admission, bounded
L1–L5 scoring primitives, same-build private truth, stripped-image verification,
and public-package allowlisting. Twenty-seven offline controls pass, including
positive/equivalent controls for all six families, single-defect artifacts, a real
absent-target binary and a paired PIE build. This is preparation evidence, not a
completed capability measurement or a model result.

## Families and observable claims

| Family | Retained behavior | Localization controls |
| --- | --- | --- |
| `record_codec` | Tagged record, little-endian count/value, fixed salt | Root, rejection branches and multiply/XOR operations, static salt reference, call chain |
| `dispatch_registry` | Byte-selected aggregation policy table | Root, empty-input branch and indirect call, conditional indirect edges, table reference, call chain |
| `numeric_aggregation` | Signed values, bounded array, ordering, weighted wrapping sum | Root, signed ordering decisions, call chain |
| `variable_objects` | Length/tag/flags header and bounded payload checksum | Root, header branches and checksum operations, call chain |
| `callback_policy` | Indirect index-dependent per-byte transform | Root, callback site/target, directional call chain |
| `session_state` | Count/checksum updates through a session object | Root, count/checksum operations and stores, call chain |

Private assembler labels no longer select semantic truth. Review found an
error-initialization instruction under a check label and an omitted XOR. The
family catalog now binds required operations and accepted context to exact
binary digests and instruction bytes; unreviewed builds fail preparation.
These regions do not establish recovery of every operation in a function. Data-reference scoring is
currently limited to retained static salt/table objects. Separate T3 and C5/C6
conditions supply pointer/object artifacts and explicit multi-instance state;
these are not implied by localization of a store or callback site. Bounded
integer-record T1/T2/T5 submission probes are implemented separately from
these localization targets. Their 35-control acceptance matrix passes, including
both original-endpoint directions, twelve paired scalar/layout counterfactual
conditions and 216 native/emulator reference comparisons.
The separate C1–C4 buffer path is described below.
Root questions now identify the complete scalar wrapper operation, including
its distinguishing postprocessing or session initialization, and exclude its
individual helpers and buffer/CLI frontends. Controls reject helper roots for
all six families. Before release, independently review legitimate alternative
roots and semantic realizations. The coordinating-agent review and its
regression controls do not replace independent acceptance. See
[the instruction review](../../../guides/localization-review.md).

## Build and truth boundaries

The initial control builds use the pinned GCC 13.3 C++ image, C++20 and `-O2`.
`noipa` and disabled sibling-call optimization keep the first condition's call
boundaries observable. Empty private assembler labels include a memory barrier;
this is an explicit instrumented build condition. Compiler-created basic-block
copies retain distinct labels, which the semantic scorer does not use. Function inlining/cloning/LTO equivalences
are not certified by these controls. Clang, Rust and Go target builds remain to
be added before claiming the planned baseline matrix.

The compiler, headers, linker, libraries, loader and Python run in the existing
closed C++ image. Explicit setup pins a small strip/objdump addon with its native
libraries. Builds have no host checkout, credentials or network. Compiler CPU,
wall time, address space, output files and captured pipes are bounded.

The private debug ELF and public stripped copy come from the same compiler
invocation. Every allocated section's address, flags, size and bytes must agree
after stripping. Program-header load mappings must also agree. The public binary must have no static symbol table or DWARF
sections. Private symbol/role locations must point to validated instruction
boundaries whose bytes match the stripped file. Dispatch-table target pointers
and indirect-call instructions are checked before creating private truth.

RVAs are offsets from the lowest `PT_LOAD` page, for both PIE and non-PIE ELF.
The absent-target control compiles out the requested behavior and its helpers;
simply deleting a business symbol would not satisfy this control.

Public packages contain exactly `target`, `prompt.txt`, `manifest.json` and
`tools.json`. Manifest fields are projected by allowlist; private entry mappings,
business symbols and nested labels are never copied. The separate T conditions
provide only the nonmeasured type/layout premises appropriate to that condition. Loader
entry, imported runtime metadata and intrinsic binary clues are documented input
conditions. Thirty projected family/capability packages are checked for leaks.

## Cache and remaining integration

Target compilation and artifact grading have separate content-addressed actions.
Compilation depends on exact original source/header bytes, worker/compiler flags,
closed compiler and binutils identities, and platform facts. A localization grade
key depends on exact submission bytes, validated private truth, its two semantic
grader modules, package initialization and the preparation interpreter fingerprint. This host preparation
key is not a closed-runtime grading attestation. Changing reports, prices or unrelated scripts does not enter
either action. Cache reuse is tested with compiler execution blocked.

Nineteen disabled task definitions are frozen for localization, independent
C17 buffer recovery and separate full-semantics C generation controls. They cover
L1–L5/C1–C4 preparation, not all nineteen sub-capabilities. Typed capture, closed
JSON grading, coordinator/cache hooks and append-only grade selection are
implemented. C controls compare 222 gold cases against the same stripped originals
with genuine host-recorded status and memory effects.

Eighteen additional T1/T2/T5 definitions use scalar/layout/ABI JSON descriptions,
three related record variants and two calling conventions. Probes link the frozen original object in both call
directions. See [type/ABI scope](../../../guides/type-abi-conditions.md).

Twelve paired C++20 recovery/generation definitions use the same six buffer
targets, contracts, vectors and original observations as C17, bringing the baseline
to forty-nine. Twelve scalar/layout counterfactual conditions bring the configured
registry to sixty-one. The supported C++ profile is freestanding and nonallocating, with
the same host-owned execution and memory comparison. See
[C/C++ pairing](../../../guides/cpp-buffer-pairing.md).

Eight matched C17/C++20 lifecycle conditions add explicit callbacks and
persistent interleaved state. Two T3 pointer-object conditions add paired physical
layouts, description-driven original-endpoint probes, extent bindings and overlap
checks. Two T4 tagged-variant/subobject conditions add active-alternative and
subobject/member-offset descriptions against unchanged original endpoints.
Two T6 indirect/object-call conditions add submitted call relationships under
complete supplied root layouts/signatures, with real virtual calls and receiver
adjustment thunks. All 28 probe and 55 coordinator controls pass.
The current registry has 75 task definitions across the existing six
families. These are neither independent program counts nor released capability
measurements. See [lifecycle conditions](../../../guides/lifecycle-conditions.md)
and [pointer/object conditions](../../../guides/pointer-conditions.md), plus
[tagged-variant/subobject conditions](../../../guides/variant-object-conditions.md).
[Indirect/object-call conditions](../../../guides/indirect-call-conditions.md)
describe the separate T6 artifact and acceptance gate.

Four advanced diagnostic definitions now select real GCC O3/LTO localization
and pinned Rust-native/Go ABIInternal transfer. They use the same task registry,
preparation cache, typed artifact capture and grading/report paths. The declared
inventory is 79 definitions, with 42 optimized-location, 40 native-ABI, 32 typed
collector and 30 registration controls passing. Expanded 61-control common
coordinator acceptance passes all 61 controls. See [optimized locations](../../../guides/optimized-localization.md)
and [native-language ABI](../../../guides/native-language-abi.md).

Matched Clang/Rust/Go reconstruction baselines,
independent validity review, model ablations and the full release gates remain
pending. New model inference stays disabled. No existing C++/Rust/Go result gains
a new capability score. See the [typed condition protocol](../../../guides/capability-conditions.md).

See [the implementation plan](../../../guides/audit-implementation-plan.md) and
[setup commands](../../../guides/setup.md).
