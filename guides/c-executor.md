# Host-owned returns for restricted C

The R6 prototype compiles freestanding C17 and executes its x86-64 instructions
under a pinned [Unicorn](https://www.unicorn-engine.org/docs/tutorial.html) engine.
The executor is independent of existing native-process grading. Existing native
C++, Rust and Go return frames remain unauthenticated.
The separate [freestanding C++20 pairing](cpp-buffer-pairing.md) compiles bounded
C++ submissions to the same supported machine-code boundary and uses this
host-owned executor; it does not change historical native-process provenance.

## Preparation and offline controls

```bash
make setup-c-executor
make check-c-executor
```

Setup uses the pinned C compiler/Python image, downloads one SHA-256-pinned wheel
when absent, and seals its Python/native modules. Native dependencies absent from
the base image are captured by content. The first prototype uses Unicorn 2.1.4
with a fixed QEMU64 virtual CPU. No provider authentication or model request is
part of setup or validation. Tests require the prepared dependencies and run
without network access. Failed or stale controls block production execution.

The semantic module digest excludes download URL, wheel filename and compression
bookkeeping. Wheel provenance is saved separately. Setup reuses pinned bytes;
`--refresh` explicitly selects newly captured host dependency bytes. Compiler and
execution actions have independent inputs: changing VM logic does not recompile
an unchanged candidate, and changing helper source does not invalidate execution
of an already compiled ELF. These stages do not alter legacy inference or grading
keys.

## Execution contract

The accepted candidate is a linked, static, little-endian ELF64 AMD64 `ET_EXEC`
compiled with the configured freestanding flags. Dynamic linking, runtime
relocations, TLS and constructors are unsupported. Public helpers implement
`memcpy`, `memmove`, `memset`, `memcmp` and `strlen`; their exact definitions are
in `benchmarks/scripts/grading/c_executor/helpers.c`. Allocation and general OS
services are absent. `memcmp` provides a comparison sign, not a glibc-specific
integer difference.

The host selects a named function, SysV integer/pointer arguments and declared
memory allocations. Permissions apply to byte ranges, including padding within
a mapped page. Overlapping input/output pointers can share one allocation.
The caller return slot, preserved registers, direction flag and floating-point
control state are checked. A jump that completes the same valid call boundary
is accepted; a particular return instruction spelling is not required.

The guest cannot make host syscalls, print scoring frames or access the host
record store. The worker receives only the recovered ELF, one invocation's
permitted inputs, explicit prior state and declared callback responses. Hidden
expected values, future vectors and the original target are excluded. The host
collects returns and allowed memory effects after a valid boundary; traps,
invalid memory effects, early termination and exhausted budgets produce explicit
non-return statuses.

Stack-passed arguments, explicit writable-image snapshots and bounded numeric
callbacks are supported. Callback events are recorded outside guest memory.
These execution primitives do not by themselves implement C5/C6. The separate
[lifecycle preparation path](lifecycle-conditions.md) now supplies explicit
create/step/reset/destroy contracts, original-program oracles and interleaved
instance controls. Coordinator enrollment and measurement release remain pending.

The emulator worker runs in its own filesystem, network and PID namespaces using
the sealed Python/library image. Compiler and emulator code, the ELF loader,
effective policy, package bytes and host platform enter action identities. Actual
worker source snapshots and library bindings are checked; image drift rejects
the execution. CPU, address-space, wall, instruction and captured-pipe bounds are
separate limits. The loader, Python, engine and host kernel remain trusted; the
prototype is not a formally verified emulator or a general native runtime.

New capability task definitions remain disabled until their own program families,
artifact validation, private truth and positive/equivalent/negative controls pass.
Native historical observations are never relabeled as authenticated guest returns.
