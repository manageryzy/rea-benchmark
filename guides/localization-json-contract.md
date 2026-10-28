# Localization JSON contracts

These are serialization rules, not hints about expected target locations. Existing answers and their original grades are preserved. Exact address notation conversion does not rebase addresses or repair locations.

## locations.json

JSON type contract (locations.json v1): use one JSON object, with no unknown or duplicate keys.
schema_version is the JSON integer 1; binary_sha256 is the target's 64 lowercase hexadecimal digits in a JSON string.
status is the JSON string "present", "absent" or "unanswered".
ALL RVAs, schema_version, and reference lengths are JSON integer NUMBERS, never strings, floats, booleans, or objects. JSON numbers use decimal notation: an observed RVA 0x1234 is submitted as 4660, not "0x1234". This is only a notation example, not a target address.
roots is an array of at most 3 integer RVAs, e.g. [4660], NOT [{"rva":4660}], NOT [{"start":4660,"end":4670}]. Only roots[0] is scored. All roots and chain nodes must be unique.
sites is an array of objects with exactly role (one identifier string), start (integer RVA), end (integer RVA). Ranges are half-open [start,end); 1 <= end-start <= 64; at most 16 sites and 256 bytes total.
dispatch is an array of at most 24 objects with exactly condition (one condition identifier string), site (one integer callsite RVA), target (one integer target RVA). site is NOT a range object.
references is an array of at most 24 objects with exactly site (integer instruction RVA), object (integer object RVA), length (integer 1..4096), access (string "read", "write", "read_write" or "address").
chain is null or an object with exactly root (integer RVA), nodes (at most 16 unique integer RVAs), edges (at most 24 unique [integer source RVA, integer target RVA] arrays). root must occur in nodes; both endpoints of every edge must occur in nodes.
Every RVA is an unsigned integer <= 18446744073709551615. Identifier strings match [A-Za-z_][A-Za-z0-9_]{0,63}; all array records must be unique. Do not add names, notes, signatures or address wrappers to these records.
For status absent or unanswered, roots/sites/dispatch/references are [] and chain is null. For status present, unknown facts may remain empty; chain may be null.
Use RVAs relative to the lowest PT_LOAD page, not absolute virtual addresses, file offsets or automatically rebased guesses. Emit exactly one fenced block named locations.json; explanatory prose is not part of the JSON artifact.

## instances.json

JSON type contract (instances.json v1): return one JSON object with exactly schema_version, binary_sha256 and instances; no unknown or duplicate keys.
schema_version is the JSON integer 1. binary_sha256 is the target's 64 lowercase hexadecimal digits in a JSON string.
instances is an array of at most 128 objects, each with exactly ranges. ranges is an array of 1..16 objects, each with exactly start and end.
start and end are JSON integer NUMBERS, never strings, floats, booleans or address objects. JSON numbers use decimal notation: an observed RVA 0x1234 is submitted as 4660, not "0x1234". This is only a notation example, not a target address.
Each range is half-open [start,end), with 0 <= start < end < 281474976710656 and end-start <= 4096. No overlapping or repeated ranges within an instance, and no duplicate instances.
Use RVAs relative to the lowest load page, not absolute virtual addresses or file offsets. Do not add role, kind, symbol names or compiler-history labels. Empty instances means no submitted realization.
Emit exactly one fenced block named instances.json. The exact UTF-8 JSON artifact must not exceed 131072 bytes.

## Offline compatibility regrading

`PYTHONPATH=benchmarks/scripts /usr/bin/python3 benchmarks/scripts/localization_report.py --regrade` uses completed, source-bound answers and the unchanged closed graders. It writes append-only revisions under `.cache/benchmark-runtime/localization-contract-20261027/`.

Only exact unsigned hexadecimal or decimal strings in declared integer fields, and unambiguous roots with an explicit `rva` field, are converted. Original metadata is retained in the conversion receipt. Ambiguous root ranges, callsite ranges, duplicate keys, unknown fields, noninteger values and invalid bounds remain ungraded format review. No target truth is used to repair submissions. Original answers and selected-grade files are never overwritten.

The current live batch keeps its frozen prompts. The tested prompt patch is installed only after its controller and report finalizer finish and all execution locks are free. Future preparation uses the new prompt identity; previous scores retain their original identities. No offline acceptance receipt is fabricated.
