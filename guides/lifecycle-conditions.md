# Explicit callback and persistent-state conditions

The C5/C6 preparation path uses two additional interfaces in the existing
`callback_policy` and `session_state` families. These are not renamed samples,
model runs or replacements for the original six buffer targets. The original
buffer builds and all frozen model-task identities remain unchanged.

```bash
make check-lifecycle-capabilities
```

This command prepares any missing pinned local dependencies, builds trusted GCC
C++20 `-O2` originals, strips copies, verifies their loaded bytes against the
same-build private DWARF/symbol images, and runs offline controls. Content-addressed
original, reference, candidate-compile, guest-invocation and grading actions reuse
valid results. All 22 controls pass. The final acceptance run forbids additional
compiler, emulator and original-program launches and reuses saved executions.
The controls forbid network connections and model harnesses.

The path is enrolled in the normal configured capability coordinator, with
separate C17/C++20 recovery and full-semantics generation profiles. The two
existing families contribute eight matched definitions, bringing the frozen
registry to 69 without changing any of the earlier 61 records. Fourteen named
capability classes have preparation paths; zero capability measurements are
released. Independent task-validity/release review and broader compiler/language
baselines remain required. All new inference stays disabled.

```bash
make prepare-capabilities
make check-cpp-lifecycle
make check-capability-coordinator
```

The C++ profile reuses the sealed compiler worker with an explicitly bound
logical header alias. It does not modify the older buffer compiler or its
action keys. Twelve C++ controls pass against the same 106 original calls.
Compilation failure is labelled `not_executed`, distinct from authenticated
guest returns. The normal named-artifact, append-only grading and inference
cache paths handle both output languages; no second benchmark runner is added.

## Invocation contract

```c
typedef uint64_t (*session_callback)(uint64_t operation,
                                     uint64_t value, uint64_t index);
int32_t reconstructed(uint8_t *state, uint32_t operation, uint32_t value,
                      uint32_t limit, uint64_t *output,
                      session_callback callback);
```

Each instance owns 64 initially zero bytes aligned to eight bytes. Its private
representation is opaque. Operations are create (0), step (1), reset (2), destroy
(3) and read (4). Two instances remain live across interleaved calls. Recreating
an active instance, use after destroy, invalid parameters and callback failure
have distinct observable statuses. Output names a separate aligned eight-byte
allocation; operations that produce no value preserve those bytes.

Only the selected instance and output may be read or written. The host checks
their byte permissions and preserves every other byte, including the other
instance and guards. State snapshots and writable guest-image state come from
the candidate's own previous valid invocation. They are never repaired with
oracle state. An execution fault fails the remaining calls in that scenario;
independent scenarios begin with fresh context and guest-image state.

The candidate worker receives the current operation, its permitted memory,
explicit prior guest state and current callback response. It receives no
original ELF, future inputs, reference output or hidden expected state. Genuine
host-captured return values, exact output bytes, callback arguments/order/count
and preserved bytes determine the score. Internal state bytes are not compared
with the original, allowing equivalent layouts and inactive representations.

Whole-task success requires every observed component. In the callback family,
C5 checks status, callback traces, preserved memory and output preservation on
nonproducing/error paths. C6 checks status, state-dependent output and preserved
memory. A wrong callback argument can therefore fail C5 while retaining correct
C6 outputs; it still fails the complete task. In the callback-free session
family, unexpected callbacks also fail C6. These component vectors are diagnostic
measurements, not independent causal estimates.

## Original family semantics and controls

The callback policy maintains an active flag, remaining quota, count and unsigned
32-bit checksum. Create/reset validate a quota from 1 through 8. They invoke
`callback(operation, value, limit)` and initialize count to zero and checksum to
`value XOR low32(response)`. Step accepts a byte, checks remaining quota, and
invokes `callback(1, value, count)`. On success it consumes one quota, increments
count and updates checksum to `(checksum * 33) XOR value XOR low32(response)`.
Destroy invokes `callback(3, checksum, count)` and deactivates the instance.
Read invokes no callback. A callback response of `UINT64_MAX` returns an error
and commits no state or output. Successful step/read writes checksum in the high
32 output bits and count in the low 32 bits. Arithmetic wraps at 32 bits.

The session family uses no callbacks. Create/reset validate a multiplier from 1
through 31 and initialize checksum to `value` and count to zero. Step increments
count and updates checksum to `checksum * multiplier + value + count` modulo
2^32. Step/read writes the same high-checksum/low-count output encoding. Destroy
deactivates that instance. The complete uint32 value domain is allowed.

Reviewed original sources and separately authored C controls use different
private layouts. The controls exercise rollback after callback failure,
callback argument/count defects, preserved error output, quota exhaustion,
destroy/recreate, reset of a used instance, overflow, global cross-instance state,
forbidden guard writes and fresh scenario boundaries. Cache controls forbid
repeated compilation/execution and verify that the six previous original-target
actions retain their saved identities. A source-bound receipt gates direct
lifecycle grading; failed or stale controls cannot authorize production grading.

The bounded caller-owned create/destroy interface has no heap allocator, general
C++ runtime, reentrant callback or concurrent execution claim. Broader lifecycle
and callback patterns need additional declared conditions.
