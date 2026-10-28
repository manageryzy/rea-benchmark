# Native language ABI preparation: T7

Status: two configured diagnostic conditions, with inference disabled. The
originals pass 60 native/reference comparisons. All 40 source/runtime-bound
package/probe controls and all 61 expanded common coordinator controls pass.
There is no model observation or released T7 score.

```sh
make build-native-abi
make check-native-abi
```

These commands use the existing pinned Rust 1.91.1 and Go 1.23.2 Linux AMD64
images, plus the separately pinned compiler/linker and emulator images. They do
not refresh those images, enable inference or call a model. Missing/changed
native ABI acceptance prevents description compilation and probe execution.
The original programs can still be built for offline preparation.

## Actual native boundaries

Both originals operate on a known three-field uint64 packet and return a known
three-field uint64 reply. The 24-byte layouts, complete semantic operation and
runtime premises are supplied. The measured unknown is their physical transfer
at this exact compiler's native boundary.

The Rust library uses ordinary Rust functions and explicit `extern "Rust"`
replacement declarations. Their physical transfer is the measured unknown.
A separately compiled Rust replacement object initially delegates to the real
original callee; probes replace that slot without changing either original
caller or original callee code. `repr(C)` supplies the record layout and does not
make the function use the C calling ABI. A distinct `extern "C"` control checks
the separately compiled stable FFI boundary. That control is never presented as
T7 success or copied into the supplied native-boundary premises.

The Go compiler reports `ABIInternal` for both original endpoints. The original
caller invokes an actual function value. A probe changes only its dispatch
descriptor, retaining both compiled endpoints. Go's R14 runtime context and the
bounded stack premise are supplied. This is neither cgo nor an ABI0 assembly
function presented as ABIInternal.

## Description-driven probes

The strict `native-abi.json` artifact has a binary digest and separate packet, nonce
and reply locations. It admits bounded register packs, indirect records and
aligned stack slots; it does not admit source, arbitrary addresses or expressions.

The closed compiler generates two complementary probes:

1. The original native caller invokes the replacement. The replacement decodes
   the claimed input, independently computes the supplied semantics, and encodes
   the claimed output. It does not decode/re-encode the same claim before calling
   the original, which could conceal symmetric ABI errors.
2. A generated bridge marshals the known logical inputs according to the claim,
   calls the unchanged original native callee and reads the claimed result.

The probe compiler receives no original source, private gold binding or hidden
vector list. Each emulator worker receives one invocation. Native execution,
emulation and a separate Python reference compare defined fields. There are
twelve reference inputs per language: 24 emulated original observations and 48
bidirectional gold checks. The 60 native calibration observations additionally
include twelve separately labelled C FFI controls.

The common task registry selects `rust_native_recover` and
`go_internal_recover` in `capabilities.json`, within the existing
`variable_objects` family category and the shared
`native_aggregate_interfaces_v1` lineage. Public packages contain only the
stripped target, static tools, prompt and manifest. They supply compiler identity,
endpoint locations, semantics and layouts, and omit the measured register/stack
bindings. The private grading specification binds the exact original action and
nonce selection. Real model dispatch requires current package and coordinator
acceptance in addition to explicit inference enablement.

## Execution and stripping scope

The native fixture loader reuses the pinned instruction, memory and return-stack
guards in a separately identified profile. It executes whitelisted original
endpoints and generated probes; it never runs Rust/Go startup in the emulator.
Image writes are forbidden after the host's checked slot/descriptor substitution.
The host collects actual result registers or defined result-buffer bytes and
checks the original endpoint bytes before and after execution. Guest stdout
cannot supply a return record. Trusted original CLI calibration runs separately
in the matching sealed native image.

The public images have no private symbol table or DWARF. Ordinary GNU stripping
retains Rust's fixed 34-byte GDB auto-loader filename; this exact marker is
allowlisted locally because startup references it. Go's normal runtime metadata
also remains. GNU strip trims sixteen trailing zero bytes from Go's data segment;
the validator proves identical effective load memory after zero-fill. Nonzero
changes are rejected. The shared C/C++ stripping validator is unchanged.

## Cache and release boundaries

Original compilation, description compilation and individual observations have
separate Merkle actions. Original/probe ELF inputs are stored as separate CAS
materials rather than duplicating large hex images in every invocation request.
Private vector selection and scoring changes do not enter compilation identity.
Unrelated scripts and the other language's original sources do not change an
original build. Loaded source, post-worker drift, request/engine bindings and
original endpoint preservation are checked before returning observations.

Aggregate grading has its own action above those individual observations. A task
passes only when every selected invocation passes in both directions. A wrong
nonce register can accidentally agree at zero; the regression retains that
partial credit while requiring whole-task failure. Changing private vector
selection reuses matching compilation and execution actions. Invalid shapes and
wrong-binary descriptions launch no probes; a probe infrastructure failure is
not converted into a model correctness score.

The restricted C executor's acceptance does not certify this new loader, general
native Rust/Go execution or historical native frames. Independent task-validity
review, broader native signatures/runtime mechanisms,
premises-only model ablations and measurement release remain open.
Heap/GC behavior, captured closure environments, unwind behavior and other
compiler versions/architectures are outside these initial integer-record probes.
