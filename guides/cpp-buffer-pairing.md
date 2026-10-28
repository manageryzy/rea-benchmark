# Paired C and original-language C++ reconstruction

The initial six C++ buffer targets now have independent C17 and C++20 recovery
conditions, plus a full-semantics generation control in each language. These
conditions remain disabled for model inference. All sixteen offline pairing
controls pass, including all 222 gold vectors across the six families. These
controls are not new model measurements. The 37-control coordinator suite also
passes, including both C++ profiles and private-only regrading with compiler,
executor and provider launches forbidden.

## One target and one behavioral contract

For each family, the C and C++ conditions use identical original ELF bytes,
function RVA, private input vectors and trusted original observations. Both
return a signed 32-bit status and write a separate length and byte buffer. The
same comparator checks the genuine return, length, exact output and every
preserved arena byte, including overlap and capacity/error behavior.

| Profile | Artifact | Output language | Semantic information |
| --- | --- | --- | --- |
| `c_recover` | `solution.c` | C17 | Analyze the known original buffer entry |
| `cpp_recover` | `solution.cpp` | C++20 | Analyze the same original buffer entry |
| `c_codegen` | `solution.c` | C17 | Complete semantics supplied in prose |
| `cpp_codegen` | `solution.cpp` | C++20 | The same complete semantics supplied |

Sessions and inference identities are separate. No condition receives another
attempt's answer or source. The public packages and prompts differ to declare
the requested output language; private-only grading revisions do not change
inference identity. Adding the C++ conditions preserves all thirty-seven prior
public task identities. Twelve additions bring the preparation registry to
forty-nine baseline definitions across six declared families. Twelve subsequent
scalar/layout counterfactual conditions bring the configured registry to sixty-one;
they do not add independent program samples.

## Restricted C++ compiler and authenticated execution

The C++ compile action uses the pinned GCC 13.3 C++ image with C++20 and `-O2`.
Only the submitted source, public declaration, byte helpers and linker script
enter the worker. The target, oracle, hidden vectors, host checkout, account state
and network are unavailable. Compiler CPU, memory, wall time and captured output
have explicit bounds. ELF admission rejects missing exports, dynamic linking,
TLS, unresolved runtime helpers and images outside the executor's memory domain.

The supported profile is freestanding, nonallocating C++20. Header-only
algorithms, `std::array`, `std::span`, lambdas, local records/member functions and
similar constructs can express the six original functions. Exceptions, RTTI,
heap/OS services and startup-dependent initialization are outside this profile.
A public C-compatible declaration fixes the five-argument buffer boundary;
the implementation itself is compiled as C++, not translated into C.

The existing host-controlled instruction emulator executes the admitted ELF.
There is no candidate stdout framing. The host collects the real return and
memory effects. This is a bounded C++ profile; historical general native
C++/Rust/Go submissions retain their unauthenticated-process limitation.

The 262,144-byte candidate-source limit excludes the trusted forced declaration
in both languages. A C executor boundary control checks a maximum-sized source
with an additional public header. Standard compile wall/CPU/memory limits are
120 seconds, 90 CPU seconds and 4 GiB for both languages. The same instruction,
memory and execution-time limits apply to their machine code.

## Commands and cache boundaries

```bash
make check-c-executor
make check-cpp-buffer
make check-capability-coordinator
make reports check-public check-reports
```

The C++ target checks the shared C-buffer controls, prepares all registered
packages and then validates the C++ pairing. The ordinary coordinator remains
the sole benchmark runner. `make benchmark` selects enabled configured profiles
once inference is explicitly enabled and the remaining release gates pass.

C++ compilation, individual host invocations and aggregate comparison are
separate actions. Compilation binds exact candidate/declaration/helper bytes,
compiler image, flags, source bounds and ELF admission bounds. It excludes
original targets, vectors, pricing, reporting and unrelated execution budgets.
Changing a vector or comparison can reuse valid compile/invocation results.
Cache hits reuse observations and never create another model trial.

The controls compare six readable, idiomatic C++ implementations with the C
controls and the same original targets. Negative controls change signedness,
sort direction, length, overlap, error writes and preserved tails. Equivalent
serialization, compiler/grade cache reuse, filesystem isolation, declaration
mismatch, source drift and missing acceptance evidence are checked separately.
