# Typed capability conditions

The permanent `capabilities-v1` suite remains under construction. The current
paths cover L1–L6, C1–C6 and bounded T1–T7 preparation, with six C++ family
categories plus pinned Rust-native and Go ABIInternal diagnostic originals.
The six C++ targets also have paired C17/C++20 reconstruction and generation
conditions. A separate [C5/C6 lifecycle path](lifecycle-conditions.md) supplies
explicit state/callback interfaces, original-program controls and matched C17/C++20
recovery/generation conditions through the normal coordinator. The separate
[T3 pointer/object path](pointer-conditions.md) uses description-driven access
probes against paired original layouts. The [T4 tagged-variant/subobject path](variant-object-conditions.md)
adds description-driven tag and member accesses, with its own acceptance gate.
The [T6 indirect/object-call path](indirect-call-conditions.md) adds actual
function-pointer, receiver, callback-context, virtual-slot and thunk probes;
its 28 probe controls and the earlier 55-control coordinator milestone pass.
[L6 optimized locations](optimized-localization.md) and
[T7 native-language ABI](native-language-abi.md) now have four configured
conditions, 42 and 40 preparation controls, 32 collector controls and 30
registration controls. All 61 expanded coordinator controls pass.
Matched Clang/Rust/Go reconstruction baselines, independent validity and the full
release gates remain unfinished. Model inference is disabled in
`benchmarks/config/capabilities.json`.

## Commands and configuration

```bash
make prepare-capabilities           # freeze public packages and private grade specs
make benchmark-plan                 # read-only native/capability selection
make check-capabilities             # localization truth, projection and scorer controls
make check-structured-grading        # closed Python artifact-worker controls
make check-buffer-capabilities       # C1–C4 against the actual stripped originals
make check-lifecycle-capabilities    # explicit C5/C6 preparation; no model calls
make check-cpp-lifecycle             # matched C++20 lifecycle; no model calls
make check-cpp-buffer                # paired original-language C++ on the same contracts
make check-abi-probes                # T1/T2/T5 frozen original caller/callee probes
make check-pointer-probes            # T3 pointer/object original-endpoint access probes
make check-variant-objects           # T4 tagged-union and subobject original-endpoint probes
make check-indirect-calls            # T6 call relationships, virtual slots and receiver thunks
make check-capability-coordinator    # typed capture, append-only grades and cache reuse
make reports                        # global reports; no model calls
```

Preparation is an action of the existing coordinator (`run.py prepare`). The
ordinary benchmark selector includes enabled capability conditions when their
suite inference flag is enabled; the default remains disabled. Planning and
dry-run preparation invoke no compilers, credentials or providers. Explicit
preparation can build and execute trusted originals in closed workers.
The normal run now prepares selected new/stale cases under its batch lock,
once per case across models. Planning retains pending/invalid cells in its
denominator without preparing them. A separate preparation command is optional
for an enabled run; see [the preparation action and controls](capability-preparation.md).
Before provider preparation or runtime-workspace creation, the coordinator
validates the private grading-spec path, content digest, target binding and
artifact/score contract. Missing or corrupted grading data cannot consume a
model attempt. Valid private-only revisions still leave inference identity
unchanged.
The enabled flag of the selected profile is checked again before materialization,
so a previously selected task cannot override a later profile disablement.

Localization, independent C recovery and full-semantics C generation are three
configured profiles. Six families yield eighteen profile/family definitions,
plus one real absent-target localization control. T1/T2/T5 add eighteen
family/convention/profile combinations, making **thirty-seven task definitions**.
The paired original-language C++ recovery/generation conditions add twelve more,
making forty-nine baseline definitions. Twelve paired scalar/layout
counterfactual conditions bring the earlier registry to sixty-one. The matched
C17/C++20 lifecycle recovery/generation conditions add eight, making sixty-nine.
Two paired T3 pointer layouts brought the registry to 71, preserving all
69 previous records. Two T4 tagged-variant/subobject layouts bring the current
registry to 73. Two T6 indirect/object-call originals bring it to 75. Two GCC
O3/LTO location conditions and two Rust-native/Go ABIInternal conditions bring
the declared inventory to 79. These are neither the nineteen sub-capabilities
nor independent program samples. See [C/C++ pairing](cpp-buffer-pairing.md).
See [the type/ABI condition protocol](type-abi-conditions.md) for isolated premises,
description-generated probes and remaining validity gates.

Public packages contain exactly `target`, `prompt.txt`, `manifest.json` and
`tools.json`. Private truth, vectors and reference output stay in independent
grading specifications. Structured tasks have no invented output language.
Present/absent variants have identical prompts, tools and public stage
declarations. Their public IDs are opaque and contain no presence labels.
Known buffer-entry RVAs are provided only to the paired C/C++ buffer conditions. Loader entry and
intrinsic runtime metadata remain documented binary clues.

Localization role truth uses exact-binary, byte-checked semantic operations,
with separately accepted local context. Assembler labels are not semantic proof.
The public prompt defines roles and dispatch condition IDs. See
[the instruction review and scoring rule](localization-review.md); independent
acceptance of alternative roots and realizations remains a release gate.

The typed task records target `source_language` separately from
`output_language`. The generic adapters' legacy `language` alias follows the
output contract: `c` or `cpp` for source reconstruction and null for JSON artifacts. Cache
validation rejects contradictory task or receipt language fields. The inference
identity continues to describe the source binary; old native receipts are not
rewritten.

## Artifact and execution boundaries

An answer has exactly one code block per declared filename, such as
`locations.json`, `solution.c` or `solution.cpp`. Extra, duplicate, unnamed, unfinished and nested
blocks fail admission. The bounded transport preserves source bytes and CR/LF;
brief evidence prose is allowed. JSON never uses a C++/Rust/Go code extractor.
Its schema and semantics are checked in a pinned closed Python worker with CPU,
memory, wall-time and captured-pipe limits. Artifact and infrastructure failures
are distinct. Worker source is copied from declared bytes, excluding host bytecode.

The new C17 condition uses this declaration:

```c
int32_t reconstructed(const uint8_t *input, size_t input_len,
                      uint8_t *output, size_t output_capacity,
                      size_t *output_len);
```

The same C-compatible function exists in the original C++ binary. Tests cover
empty/malformed records, signed boundaries, ordering, CR/LF/NUL/invalid UTF-8,
output capacity, status/length, overlapping buffers and preserved memory. The
output-length allocation is aligned and separate. The RE condition supplies
the memory domain without formulas; the independent generation control supplies
the complete semantics in prose. They use the same binary/vectors but fresh
sessions and different public packages. Control source is never distributed.

Reference observations come from the **same stripped original** in the pinned
C++ image; its allocated code/data and load mappings match the private debug
build. The reference protocol verifies IDs, order, status and byte lengths.
Candidate C and C++ run under the separate host-owned emulator, receiving only one
current invocation. The original binary, expected answer and future vectors stay
in the parent. Comparison covers genuine typed return, exact length/output and
every preserved arena byte. Guest stdout cannot manufacture a return record.

Gold and equivalent controls cover all six families. Single-defect controls
change signedness, sorting, length, alias handling, preserved tails and capacity
error writes. Capability tags describe jointly covered checks, not independent
causal estimates; component outcomes identify status, length, value and
preservation errors. The supported output profile is freestanding C17 with the
declared byte helpers. The paired C++20 compiler accepts header-only algorithms,
arrays, spans and local methods in a freestanding, nonallocating profile. Neither
profile supports arbitrary native runtimes or claims exact original structures.
See [the paired execution contract](cpp-buffer-pairing.md).

## Cache and preservation rules

Four-file public packages and private grading specifications have separate
digests. Grader/vector changes do not request inference. Typed grades append under
`grades/capability-artifacts-v1/<grading-id>/`, selected by an explicit index.
Answers, original tasks/receipts/completion records and old grades stay unchanged.
Each derived record preserves captured artifacts, action identity and private-spec
binding. Storage/report code is separate from semantic capture/scoring inputs.

The coordinator controls reuse a fixture twice with providers forbidden, change
only its private root oracle, and verify a new grade with unchanged inference
identity and original files. Parent comparison changes can reuse valid child
compile/execute actions. Controls/cache hits are not new model samples.
The selected capability IDs enter the grading action because changing the
scored stages changes its totals. A selection revision appends a new grade
without changing inference or discarding the previous grade.

The global report publishes bounded preparation status and control freshness.
The 110 existing model-result rows retain correctness, readability, tokens, cost
and provenance. Public HTML excludes task packages, private truth, raw provider
JSON/reasoning, candidate traces and credentials.
