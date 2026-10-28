# Tagged variants and subobjects: T4

Status: the closed execution path and two task definitions are implemented in
the existing coordinator. All 24 source/runtime-bound controls and all 51
coordinator controls pass; inference stays disabled. Offline controls are not model performance or released capability scores.

## Reproduce the controls

```sh
make check-variant-objects
```

The target prepares the existing pinned compiler/executor images and runs 24
source/runtime-bound controls. It makes no model calls. A complete successful run
saves `benchmarks/config/variant-object-validation.json`; missing, failed or stale
controls refuse production grading. The receipt does not enable inference.
Original builds, generated descriptions, native calibration, emulator observations
and aggregate grades use separate content-addressed actions and reuse valid
caches.

## Original programs and task scope

Two layouts of the existing variable-object family contain an unsigned-32 tag,
two semantic unsigned-64 subobjects, and a union with a signed-64 number or a pair
of signed/unsigned 32-bit values. The source uses actual nonvirtual bases and
active union alternatives. The paired layout changes base order, tag values,
union placement and pair-member order while retaining the same semantics.

Known caller/callee locations and the SysV AMD64 inner signature isolate T4 from
localization and call-ABI recovery. Scalar kinds and the relative zero offset of
each single-field semantic subobject are supplied premises. Root offsets, tag
mapping and payload placement remain measured. Nominal C++ inheritance, erased
source names, exact unused allocation size and unidentifiable scalar signedness
are not claims of this condition.

Numbers and pairs update the state and their active payload with defined
unsigned wrapping operations. Unknown tags return an error without changing
semantic data. The cookie, tag, protected descriptor bytes and guards must remain
preserved. No expected semantic value is taken from an inactive union member or
unspecified padding.

## Submitted description

The task-scoped `types.json` version 1 has these fields:

| Field | Meaning |
| --- | --- |
| `schema_version`, `binary_sha256` | Schema and exact target identity |
| `size`, `alignment` | Bounded root extent and compatible alignment |
| `tag_offset` | Byte offset of the known unsigned-32 discriminant |
| `subobjects` | Unique `cookie` and `state` roles with root-relative byte offsets |
| `payload` | Root-relative offset, extent and two active alternatives |
| Number alternative | Role, discriminant and relative signed-64 member offset |
| Pair alternative | Role, discriminant and relative first/second member offsets |

Descriptions cannot contain source, arbitrary identifiers or expressions. The
parser rejects unknown fields, duplicate roles/tags, boolean numeric values,
overlapping live roles, impossible extents and overlapping members within one
alternative. Alternatives share union storage. JSON order has no layout meaning.
Extra unused root tail space and observationally equivalent descriptions can pass.
Unused tail cannot claim an extent that overlaps a separate live tag/subobject.

## Two independent original endpoints

The compiler worker parses the actual artifact and generates concrete tag tests,
subobject loads/stores and payload accesses inside the closed pinned image. It
links the unchanged original/helper objects in both directions:

1. The frozen original caller invokes a generated replacement.
2. A generated bridge invokes the frozen original callee.

The host-owned emulator compares a 64-byte canonical observation: status, state,
cookie, active payload, active-kind identity, tag preservation, descriptor
preservation and guards. Wrong claims can produce wrong values or memory faults;
format validity alone does not earn behavioral credit. Guest stdout cannot supply
the observation or a score. Incorrect interoperability hypotheses are checked as
fixed-machine executions, not asserted to be valid ISO C++ declarations.

An independent native calibration links only the frozen original/helper objects
and a trusted CLI. It never loads a candidate description or its generator.
Sixteen nonces per layout cover both active alternatives, unknown tags, signed
boundaries, rotations by zero/nonzero counts and large unsigned values. Native
and emulator observations must agree with a separate Python semantic oracle.

## Cache and provenance controls

The shared object-probe engine supports these new object conditions without
changing the existing pointer or integer-record implementations/action identities.
Each worker stage binds only its selected source closure, request, compiler and
policy. An original action includes its selected header and logical alias, not
unselected headers, private gold descriptions or the probe generator.

Private target metadata must match the verified original action's actual CAS
outputs. Replacing object bytes and recomputing their digest cannot substitute a
different endpoint. Before/after worker checks reject source, policy and runtime
drift. Loaded controller, facade and contract modules must still match their source snapshot; a source
edit requires restarting that process. Parent grades recheck their action before
returning a cache hit or committing new results, preventing a changed contract
from being published under an older action.

Controls include correct/equivalent artifacts, wrong alternatives, base/payload/
member offsets, blind cross-layout descriptions, unknown-tag preservation,
malformed/wrong-binary submissions, object/runtime tampering, isolated generation,
cache hits, private-vector revisions and late source/contract drift. A cold native
control with the candidate generator disabled checks calibration independence.
One direction can accept a particular error-path hypothesis while the other
rejects it; overall success still requires every check in both directions.

## Remaining gates

The public/private package helper, configuration, typed collection, registered
build metadata and global reporting use the existing coordinator. The two new
definitions bring the registry to 73. Production grading requires the current
24-control receipt. The current coordinator, typed collector and registration
suites pass 51, 29 and 28 controls respectively. Publication/privacy and clean
report reproducibility are verified separately.
Independent task-validity review, premises-only model ablations, broader families
and the shared release gates remain required. No model ablation runs while the
experiment stop remains in force. T6 indirect/object calls and T7 language-native
ABIs are separate conditions; this union/subobject task does not implement them.
