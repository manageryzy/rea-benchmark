# Localization instruction review

The initial GCC `-O2` localization truth had valid instruction addresses but an
incorrect semantic association. In the record codec, the assembler check label
landed on the default error-return initialization at RVA `0x1854`. The actual
rejection branches are at `0x185d` and `0x1862`. The value-mix role included the
multiplication at `0x186c` but omitted the salt XOR at `0x1875`.

REA/Ghidra and stripped-target disassembly confirmed this defect without running
the target or calling a benchmark model. The codec locations were recorded from
public evidence before comparison with private truth. Other family functions
used private symbols to scope the review of their stripped instruction bytes.
This is a coordinating-agent review, not independent human or panel acceptance.

## Truth and scoring

The family catalog now records byte-bound semantic instruction reviews for all
six frozen GCC targets and the dispatch PIE control. An exact binary digest
selects the review. Every required and contextual instruction must match the
final target bytes and an instruction boundary. An unreviewed binary fails
preparation; assembler labels no longer supply semantic role truth.

Each role separates required operations from optional, legitimate local context.
For example, both rejection branches are required for the codec check; including
their comparisons is also valid. Initializing an error result is not a check.
The joint public prompt defines every role and the dispatch condition IDs.

L2 coverage is the fraction of required instruction starts submitted under the
correct role. Precision is the fraction of submitted instruction starts within
that role's required operations or reviewed context. Their harmonic mean is the
reported L2 F1. Context alone has zero coverage and zero F1. Unrelated operations,
unknown roles, invalid instruction boundaries and non-code claims reduce
precision. L1 and L3–L5 retain their separate metrics.

`make check-capabilities` passes 27 controls, including both codec regressions,
all six correct artifacts, legitimate context, a contiguous comparison/branch
range, exact binary/byte binding, public role definitions and partial-instruction
rejection. Closed artifact-worker controls remain a separate gate.

## Release boundary

The repaired localization prompts and private truth have new task/grade
identities. Old packages and grading records remain on disk. No historical
native model result is reclassified, and no localization model result exists to
regrade. The build and native inference actions exclude this review data.

Independent review of alternative complete roots and semantic realizations,
L6 optimized-location cases, broader compiler/language conditions and the full
capability release gate are still required. These controls do not complete the
general localization benchmark or enable model inference.

An independent review must record the reviewer, exact stripped binary digest,
build identity, public contract and truth/scoring versions. For the six GCC
targets and the dispatch PIE control, the reviewer should:

1. Locate the requested behavior from the public stripped target and record the
   evidence before comparing it with private symbols or labels. Inspect plausible
   alternative complete roots and similar distractors, rather than confirming
   only the authored root. Verify real absence separately from inlining.
2. Review each semantic role at instruction boundaries in the final stripped
   bytes. Separate required operations from permissible local context. Check both
   false acceptance and false rejection, including the codec's default-error
   initialization, both rejection branches and the salt XOR.
3. Check alternative valid ranges, unrelated appended instructions, partial
   instructions, wrong roles and overreported ranges against coverage, precision
   and the published F1 rule. Verify RVA normalization on both ELF forms; an
   address-only match is insufficient evidence of the claimed semantic role.
4. Review L6 optimized instances separately against the exact optimized/LTO
   artifact. Identify valid inline/clone/cold/thunk realizations and unresolved
   alternatives; original symbol names alone do not establish completeness.

Keep findings, unresolved alternatives and their resolutions bound to the reviewed
version. Publish only the approved aggregate review status through the report's
allowlist. Author controls and an unsigned checklist remain preparation evidence,
not an independent review or a released capability measurement.
