# Closed candidate-grading runtimes

Candidate grading uses a pinned content-addressed toolchain image, independent
of inference runtime attestation. A target's compiler remains a separate build
condition: the compiler described here compiles the recovered candidate.

## Prepare and verify

```bash
make setup-grading
make check-grading-runtime
make check-measurement
```

The first command captures the configured C, C++, Rust and Go toolchains under
`.cache/benchmark-grading/v1/`. It performs actual synthetic compilation and
execution controls. Subsequent invocations reuse pinned images and current
validation receipts. No credentials, provider authentication or model requests
are needed. `make benchmark`, `make regrade` and the measurement checks depend
on this preparation; planning and report rendering do not capture toolchains.

The language-specific versions and permitted platform are in
`benchmarks/config/grading-environments.json`. The initial environment uses GCC
and G++ 13.3.0, Rust 1.91.1 and Go 1.23.2 on Linux x86-64. C++11/14 string ABI
variants and C++20 are candidate compile conditions within the C++ image. Target
GCC versus Clang and optimization variants retain their independent identities.

To select newly installed toolchain bytes, explicitly refresh the corresponding
image. Updating the configured version is necessary when its version changes:

```bash
/usr/bin/python3 benchmarks/scripts/grading/toolchains.py --language cpp --refresh
make check-grading-runtime
make regrade
```

A refresh retains old content archives. Installing unrelated host packages or
editing reports does not select a new image. A policy change requires preparation
before new grading; planning remains read-only. Missing or stale controls refuse
candidate evaluation.

## Identity and execution

The image manifest hashes exact compiler bytes, compiler helpers, assembler,
linker, startup objects, headers, standard libraries, dynamic libraries, ELF
loader, Python interpreter and standard library, and bubblewrap executor. It
also records directories, normalized file modes, symlink selection, compiler
version and the effective environment. Archives deduplicate identical content;
compression and archive bookkeeping do not enter the semantic image digest.
Rust uses its captured sysroot and GCC linker. Its optional installed
`rust-objcopy`, whose LLVM RUNPATH is unresolved, is excluded and is not used by
the grading compile commands. Go uses its captured source/tool distribution with
CGO, network modules and automatic toolchain downloads disabled.

Archive extraction checks the content digest, inventory, each payload hash,
path validity, file type, mode and normalized timestamp. The extracted image is
fully hashed before first use. Exact file and directory signatures, including
inode and ctime, are checked before and after execution, catching same-size
edits even if mtime is restored. Model code cannot modify the read-only image.

The outer worker starts with the pinned loader and executor, a private network,
PID and filesystem namespace, and explicitly selected environment variables.
Its Python interpreter loads freshly copied declared source files under
`-I -S -B`; host project bytecode and site customizations are absent. Nested
compiler/candidate namespaces expose the image and their private working tree,
not the worker's oracle, source snapshot or helper cache. Existing per-process
CPU, address-space, file and descriptor limits and wall/captured-pipe bounds apply.

New grading actions bind the image digest, kernel and CPU capabilities, candidate,
contract, vectors, limits and semantic grader sources. Grading source drift
between action preparation and execution is rejected. The trusted Go import
helper is derived from its frozen source and compiler identity and checked
against its saved binary digest before reuse.

## Migration and evidence limits

```bash
make regrade
make reports
make pages
```

Regrading creates `grades/exact-returns-v3/<grading-id>/` and updates the selected
grade index. It preserves original answers, candidates, grades and prior derived
records. A grading change never requires a new model attempt. Runtime evidence
is recorded even when candidate compilation or behavior fails; rejection of a
missing source block has no candidate execution to attest.

The global measurement page reports saved, source-bound offline controls and
the selected grades that contain closed-image evidence. These are local receipts,
not signatures from a remote service. Historical grades are not retroactively
certified, and a privileged host can still alter a running process between
checks. Native return frames remain unauthenticated; R6's host-owned executor
is a separate unfinished requirement. The new L/C/T capability tasks are also
separate and remain disabled until their own gates pass.
