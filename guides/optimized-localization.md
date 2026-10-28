# Optimized location preparation: L6

The two original programs are real GCC 13.3 AMD64 builds: separate-translation-unit
`-O3`, and the same sources with GCC LTO. Public inputs are stripped ELF files.
The two diagnostic conditions are declared in the existing task inventory;
all 61 expanded coordinator controls pass, and inference remains disabled.
It adds no model observation or released capability score.
All 42 source/runtime-bound offline controls pass. The gate records 216 native
reference observations across the two builds, with no model API calls.

```sh
make check-optimized-locations
```

The command prepares a separate pinned LTO tool bundle, builds only missing
original actions and runs offline controls. It never enables model inference.
`make build-optimized-locations` builds the two originals without running controls.
The tool bundle extends a private copy of the existing compiler image. It never
refreshes the shared GCC image or changes historical compiler identities.
Matching compiler, linker, LTO component and worker contents enter the new action
identities. Review data and private vectors do not enter original-build actions.

## What the fixtures actually contain

| Transformation | Evidence checked in the final binary |
| --- | --- |
| Inlining | Two distinct arithmetic bodies, with same-build inline-instance DWARF |
| Specialization clones | Actual `.constprop` bodies reached by distinct wrappers; the different-salt specialization is a negative control |
| Cold partitioning | A real hot/cold split, a checked conditional destination and the arithmetic helper reached by the cold path |
| Receiver thunks | An actual secondary-base adjustment followed by a direct transfer to the original method |
| Cross-unit LTO | The non-LTO program retains the cross-unit transfer; LTO removes that boundary and records the inlined origin |

The scalar operation is `rotl64(input XOR salt, 7) * 33 + 109`, with unsigned
64-bit wraparound. Near-misses change the salt or rotation. Every original build
executes nine selectors on twelve calibration inputs, including the rare cold
branch and integer boundaries: 216 native calls across both builds are compared
with a separate Python implementation.

DWARF is used to identify physical source instances, not to assert their semantic
equivalence. A bounded DWARF 4/5 reader follows concrete ranges, checks executable
memory and rejects malformed origins/ranges. A separate exact-instruction review
checks arithmetic, branch targets and receiver adjustment against final bytes.
The receiver equivalence has an explicit calling premise: the interface pointer
is owner+16, owner+8 contains the XOR salt, and the value is the second SysV
argument. It is not an unconditional equivalence between unlike signatures.

## Diagnostic artifact and score

The proposed `instances.json` contains its version, binary digest and a list of
instances, each containing half-open RVA ranges. It does not require the solver
to guess original names or compiler-history labels. Those categories are private
analysis factors: stripped equivalent code need not reveal how the compiler
produced it.

The grader checks instruction boundaries, requires the arithmetic/control-flow
sites and permits reviewed returns/entry adapters as optional context. A global
one-to-one matching prevents a single broad instance from receiving credit for
multiple realizations. Missing cold paths or adjustments lose coverage;
near-misses, unrelated code and partial instructions lose precision. Equivalent
ordering and omission of optional return instructions preserve a full score.
The receipt is source/runtime bound and explicitly disallows release acceptance.

Verified receiver wrappers and dispatch sequences are optional context. The LTO
inline receiver adjustment is accepted as an alternative to the standalone thunk;
neither path must be guessed from compiler-history labels. Closed grading derives
the reviewed truth in a pinned worker from the bound original-build diagnostics.
Host-side changes to truth or scoring cannot substitute for that worker. Extra
or missing instances prevent whole-task success, and a cached grade launches no
new workers. Rehashed false public premises and private truth tampering are
rejected by frozen-package validation.

## Remaining acceptance

The exact-instruction review is a coordinating implementation review. Independent
review of completeness, alternative realizations and public premises remains
required. Hint-free public packages pass frozen-boundary and tampering controls. Matched compiler/language
conditions, model ablations and measurement release are unfinished. The existing
model stop flags remain effective; these controls use no paid model calls.
