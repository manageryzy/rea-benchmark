# Pointer and object-role preparation

T3 is a submitted-description condition in the existing capability suite. The
four `variable_objects` profiles pair two physical layouts with one-level and
two-level pointers. They share the `pointer_object_interfaces_v1` lineage and
remain conditions of one interface family. Capability inference remains disabled.

## Configuration and commands

`benchmarks/config/capabilities.json` selects `pointer_recover` and
`pointer_recover_counterfactual`, plus `pointer_topology_recover` and
`pointer_topology_recover_counterfactual`. `benchmarks/config/pointer-probes.json` pins
the trusted original/probe compiler flags and bounds. Use the existing entrypoint:

```sh
make check-pointer-probes
make prepare-capabilities
make check-capability-coordinator check-capability-registration
make check-capability-measurements
make reports pages
```

These checks use authored descriptions and cached machine executions. They do
not call a model or resume experiments. `make benchmark` remains the ordinary
entrypoint if experiments are explicitly authorized later; no T3 runner is added.

## Task and submitted artifact

The public package contains exactly `target`, `prompt.txt`, `manifest.json` and
`tools.json`. The stripped GCC/C++20 `-O2` static ELF has a known SysV AMD64 inner
signature and supplied caller/callee locations. Three scalar roles are unsigned
32-bit length, capacity and produced. Two roles are source/destination pointers.
The measured offsets, pointer depth, extent bindings and alias claim are absent
from supplied facts. Source, object code, layout selectors, vectors and reference
observations stay private. All four public prompts, tool catalogs and nonmeasured
premises agree byte for byte; binary identities and locations are intrinsic to
the selected target.

Submit a `types.json` block with the task-scoped pointer-object schema, version 1:

| Field | Contract |
| --- | --- |
| `schema_version`, `binary_sha256` | Version 1 and the supplied binary identity |
| `size`, `alignment` | Bounded object extent and supported alignment |
| `fields` | Five unique roles, each with `kind` and physical byte `offset` |
| Pointer-field additions | `indirections` 1 or 2, `access` read/write/read_write, `extent` length/capacity/produced |
| `aliasing` | may_overlap, disjoint, or identical_or_disjoint |

Unknown properties, boolean numeric fields, overlapping/out-of-bounds fields,
missing roles and unbounded descriptions are rejected before machine execution.
This task-scoped schema is distinct from the T1/T2 record graph: the immutable
task contract selects its parser and generated probes. No submitted source is
accepted. JSON field order has no physical meaning.

## Independent original endpoints

The closed compiler generates bounded access probes from the actual description.
Offsets drive real loads/stores, pointer depth drives dereferences, extent roles
drive bounds, and access/alias permissions constrain the operations. The probe
links unchanged original caller/callee objects in both directions:

1. The original caller invokes the generated replacement.
2. A generated bridge invokes the original callee.

The host compares the defined 128-byte observation: a bounded memory arena,
status, produced length, preserved descriptor bytes and guards. Incorrect claims
can cause mismatched bytes, invalid access or a machine fault; format validity
alone never earns a task pass. Guest stdout cannot provide return records.

Fourteen nonce vectors exercise disjoint buffers, partial overlap in both
directions, identical pointers, zero length, short/full buffers, capacity errors,
null errors, one-byte operations and large unsigned nonces. Original native
calibration links only frozen original/helper objects and a trusted CLI. It uses
no submitted description or generated probe. The native reference, host emulator
and a separate Python semantic oracle must agree on all 56 reference calls.
Gold descriptions then pass 112 checks across four targets and both directions.

Equivalent field ordering, compatible alignment, extra unused tail extent and
sufficient access supersets are accepted. This is observable pointer/object-role
recovery, not exact unused allocation size, erased ownership or a T2 size score.
Two originals exercise depth one; two dereference separate source/destination
pointer cells before accessing byte storage. The original caller also detects
pointer-cell mutations. These conditions do not cover every pointer topology.

## Controls and cache identity

Twenty-seven source/runtime-bound controls cover correct/equivalent descriptions,
role and extent swaps, wrong pointer depth/access, overlapping regions, descriptor
corruption, alias restrictions, malformed/wrong-binary artifacts, tampered
objects/entry points, closed-worker generation, cache hits and fail-closed gates.
A fixed description rebound to the other layout fails in both directions.
Additional controls reject depth-one claims on two-level originals and expose a
replacement that preserves the byte result but mutates a pointer cell. Warm-cache
controls forbid compiler and emulator calls. Existing one-level original actions
keep the same identities when the unused topology sources change.
These are deterministic controls, not premises-only LLM ablations or an
exhaustive proof that no guessed equivalent description exists.

Original compilation, submitted-description compilation, native calibration,
host invocation and aggregate grading have separate Merkle actions. Each stage
binds its selected inputs and pinned runtime. Probe generator changes do not
invalidate originals; unrelated layout headers and private gold answers do not
enter an original action. Vector changes append a grade and reuse inference,
compiled probes and matching invocations. Parent cache hits launch neither a
compiler nor the emulator. Historical packages and grades are retained.

The normal coordinator validates the private spec before provider preparation,
captures the named artifact, appends typed T3 scores and reuses valid inference.
The global report shows preparation controls, configured denominators and safe
original-build metadata. Controls create no token, price or model observations.

## Remaining release gates

The two-level original sources are
[indirect.hpp](../benchmarks/suites/capabilities-v1/sources/pointers/indirect.hpp),
[indirect_reordered.hpp](../benchmarks/suites/capabilities-v1/sources/pointers/indirect_reordered.hpp)
and [indirect_original.cpp](../benchmarks/suites/capabilities-v1/sources/pointers/indirect_original.cpp).
The profiles are configured with inference disabled. Their task packages use
the ordinary preparation entrypoint; the main snapshot now verifies all 153
definitions and 131 source bindings. Full 70-check coordinator acceptance
remains pending. The 27-check pointer receipt
certifies the exact source/runtime closure exercised by these controls.

Each descriptor selects separate pointer cells before reaching the source and
destination byte ranges. The authored caller extends descriptor preservation to
those cells without serializing their addresses. Pinned native/emulated/Python
calibration and description-driven probes pass in both directions. Generated
pointer-cell reads preserve null handling and use byte-copy loads. Coordinator,
registration and global-report gates must all be current before acceptance.
This extension does not authorize a model run.

T3 remains unreleased alongside the other L/C/T tasks. Independent task-validity
review, premises-only model ablations, broader semantic families and compiler/
language baselines remain required. This condition does not implement T4 active
variants/subobjects, T6 indirect calls or T7 Rust-native/Go-internal boundaries.
See the [full implementation plan](audit-implementation-plan.md).
