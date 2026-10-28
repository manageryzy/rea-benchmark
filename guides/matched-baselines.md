# Matched compiler and language baselines

These offline controls extend the existing six capability program families;
they do not create model runs or released capability scores. Main capability
inference remains disabled. Existing historical results are preserved.

## Reproduce

```sh
make build-matched-baselines
make check-matched-baselines
make check-rust-buffer
make check-go-buffer
```

The first command prepares stripped originals. The second compares their
observable behavior with the existing GCC originals, an independent Python
implementation and C17/C++20 candidate controls. The third and fourth check the
separate, restricted Rust and Go candidate paths. These commands admit local compilers and sealed
workers, and forbid model providers. They reuse content-addressed actions;
rerunning a successful warm control does not create new model observations.
The full `make check` entrypoint includes these controls as well.

The existing `benchmarks/config/capabilities.json` declares 72 paired conditions:
six families, three original compilers, C or original-language output, and
recovery or full-semantics code generation. They use the normal
`make prepare-capabilities`, `make check-capability-coordinator` and
`make benchmark` entrypoints. There is no separate matched experiment runner.
Global inference remains disabled; `make benchmark` must honor the experiment
stop. Preparing tasks or checking fixtures does not authorize model calls.

The coordinator validates named source artifacts, exact original/reference
bindings and current compiler/executor controls before provider preparation.
Offline fixtures exercise both harness routes without a model transport, then
forbid compilers and machine workers during cache replay. Altered private vectors
reuse the same inference, compilation and matching individual observations;
altered oracles fail admission. These checks validate the pipeline, not the
ability of an unexecuted model to solve the tasks.

## Original programs

Each of `record_codec`, `dispatch_registry`, `numeric_aggregation`,
`variable_objects`, `callback_policy` and `session_state` has three additional
compiler conditions:

| Compiler | Optimization | Interface used by these controls |
| --- | --- | --- |
| Installed Clang 20, captured into a pinned SDK extension | `-O2`, C++20 | x86-64 SysV C buffer boundary |
| Pinned Rust compiler | optimization level 2, Rust 2021 | `extern "C"` buffer boundary |
| Pinned Go compiler | compiler default | Go function reached through its trusted CLI |

The Go build records its actual compiler flags. It is not labeled `-O2`. Rust
FFI controls here do not establish Rust-native ABI recovery; that has separate
T7 tasks and artifacts. Callback/session buffer conditions start afresh on each
call; the existing C5/C6 lifecycle conditions test persistent state separately.

All sixteen matched-matrix controls pass, including warm execution with compiler,
original and candidate workers forbidden. Each original is checked on exact signed status, output length and the complete
2,048-byte arena. The 1,026 comparisons include signed numeric values, malformed
input, capacity errors, overlapping input/output, arithmetic overflow and a
write ending at the final arena byte. Compilation receives selected original
sources; original execution receives cases without expected answers. The parent
performs comparison. Candidate execution receives only one invocation.

Clang's SDK extension declares a single reviewed `liblzma.so.5` override. It
copies that file before replacing it and records both content digests; it never
modifies the pinned base SDK. Previous SDK identities and cached results remain.

## Restricted Rust reconstruction

The restricted Rust profile accepts a single Rust 2021 module defining:

```rust
unsafe extern "C" fn reconstructed(
    input: *const u8,
    input_len: usize,
    output: *mut u8,
    output_capacity: usize,
    output_len: *mut usize,
) -> i32
```

The trusted wrapper supplies `no_std`, a bounded-failure panic handler and a
compile-time signature check. The pinned compiler produces a static library;
the resulting ELF must pass the existing strict loader. The host emulator owns
returns and memory observations. This supports nonallocating core operations,
arrays, slices, iterators and sorting; it does not admit arbitrary Rust runtime,
heap, TLS, syscalls or operating-system services.

All seventeen Rust controls pass, including 342 exact original/candidate
observations, signedness/order/length defects, forbidden memory effects,
syscalls, panics, wrong signatures, modified oracles and worker-free warm reuse.
Execution-source changes alter the aggregate grading identity while preserving
the compiler action.

Compiler identity depends on the candidate source, exact profile, selected
compiler/linker sources and pinned SDKs. The compiler never receives vectors or
the original oracle. Grading separately binds the exact cached original build,
reference observations, vectors, compiler action and execution profile. A supplied
oracle must match the independently restored original observation. Changing
grading inputs does not invalidate an unrelated compiler action.

The source-bound control receipts live in ignored local configuration files.
Passing these offline controls establishes only the exercised contracts. These
additional baselines are declared as disabled model task definitions. Their
preparation and coordinator receipts must be current before any later authorized
inference. Independent validity review, model ablations and release acceptance
remain pending.

## Restricted Go reconstruction

A separate restricted path compiles a Go `package main` with a typed
`reconstructed` function accepting `unsafe.Pointer`, `uintptr`, `unsafe.Pointer`,
`uintptr` and `*uintptr`, and returning `int32`. The pinned compiler keeps that
function as a real ABIInternal endpoint. A trusted Go syntax-tree helper checks
decoded imports, package and compiler directives before candidate compilation.
Quoted words in comments and ordinary strings do not count as imports.

The host emulator supplies the five actual Go argument registers, a bounded
stack, the Go R14 context and five argument spill slots. The root return slot,
R14 context and system/control registers remain guarded. The host captures the
signed return and exact arena/length effects. It never executes the program's
startup or trusts a candidate stdout frame. Nominally writable image segments
are read-only under the host memory hooks. Syscalls, heap/GC use, global writes,
runtime initialization and stack growth are outside this profile.

All eighteen Go controls pass, including 342 exact original/candidate
observations, wrong signedness/order/length, memory faults, panics, global writes,
signature failures, syntax-tree import/directive admission, modified oracles and
worker-free warm reuse. This restricted reconstruction condition does not establish general
Go runtime support. Its compilation, one-call observation and aggregate grading
have separate action identities, and its original oracle is independently bound
to the exact cached Go build. Keep it distinct from C FFI, the C executor profile
and the T7 ABI-description tasks.

`make reports` projects these source- and pin-bound receipts into the global
preparation table and workflow diagram. Public pages contain only aggregate
control status and counts. The new configuration adds disabled model cells;
historical model rows, readability, tokens and cost remain unchanged. The report
derives enrollment from its saved inventory. Older public snapshots do not gain
new configured tasks or acceptance claims when rendered with the updated generator.
