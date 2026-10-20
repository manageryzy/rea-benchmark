# STL container and C++11/14 expansion

Build, feature probe, report and verification commands use local caches and
launch no model inference. The existing C++11/14/Rust comparison uses twelve tools;
execution is currently stopped. Shared orchestration and reporting are described
in [the benchmark guide](../../README.md). The completed six-run C++20/Rust/Go
pilot remains the historical eight-tool comparison.

`authoring/containers.cpp` covers all 16 C++11 container templates/adaptors:
array, vector, deque, forward_list, list; set, multiset, map, multimap;
unordered_set, unordered_multiset, unordered_map, unordered_multimap; stack,
queue and priority_queue. Separate handlers cover vector<bool>, basic_string,
bitset and valarray. This covers types and selected operations, not every API
overload, allocator, character type or template combination.

One request combines dispatch and record parsing with a container workload.
There are 22 handlers: 20 container/related-type workloads, a C++11 ownership,
exceptions and cleanup workload, and a C++14 grammar workload. The latter is
an explicit ERR:STANDARD control in C++11. C++14 grammar lives in a separately
included file so even its digit-separator tokens do not enter C++11 parsing.
The public native signature uses vector rather than C++20 span.

Builds use strict -std=c++11 or -std=c++14, -pedantic-errors, -Werror, -O2,
inlining enabled, and both values of _GLIBCXX_USE_CXX11_ABI. Separate translation
units cross a std::string-returning boundary using matching ABI settings. This
varies actual library layouts/mangling; it is not cross-ABI interoperability.
Each symbol-retaining original and stripped copy must pass 2,842 independent
Python oracle checks, have identical .text and no regular symbols/debug info
in the stripped copy. Address hints identify handlers without naming behavior.

C++11 points include variadic templates, scoped enums, lambda captures,
unique_ptr/moves, deleted copy, virtual override/final, constexpr/static_assert,
noexcept and exception paths with specified reverse cleanup. C++14 adds generic
lambdas, move-only init capture, make_unique, return deduction, decltype(auto),
constexpr loops, binary literals, digit separators and transparent comparators.

`authoring/modern.cpp` supplies optional native fixtures for flat_map,
flat_multimap, flat_set, flat_multiset (C++23), inplace_vector and hive (C++26).
The feature probe records actual compiler diagnostics. Unsupported types are
not counted as executed or emulated with another container. Their model cells
are excluded from the current future matrix. span/mdspan are separate views;
pmr names and allocator variants are distinct future axes, not new templates.

```sh
make build-fixtures
make build-cpp-points
make check-fixtures
make verify-fixtures
make reports
```

The authorized model cohort uses:

```sh
/mnt/h/work/rea/bin/benchmark run --suite stl-native-v2 --model glm-5.3 --dry-run
```

It contains four C++ dialect/string-ABI cases plus Rust, with both models, one
first attempt per cell. At most two processes share the host, one per model.
Prompts and binaries are frozen before inference. Completed attempts and their
source/usage/grade receipts are reused only after content hashes match. Global
attempt claims and a cohort lock prevent duplicate launches. Interrupted
inference is not retried; completed inference can be graded offline without a
new model request. Provider retries are disabled. This command does not launch
the remaining full-suite matrix. The same 60-call, 30-minute and 128,000-token
common-ceiling limits apply to both models, with the Codex wire-cap limitation
retained and disclosed.

The build and verifier use SHA-256 keys and check artifact hashes before cache
reuse. Compilation and oracle validation have separate caching. Repeating an
unchanged command uses its validated receipt; it does not repeat a model run.
The model comparison is copied byte-for-byte into the report. Cached answers
are never edited or relabelled as attempts on the new profile.

The separate `complex-native-v1/config/pilot-data-tools-v2.json` enables four
additional read-only tools: read_bytes, search_bytes, list_strings and
search_strings. It keeps the selected-target restriction, bounds reads to
4,096 bytes and searches to 200 hits, and denies document overrides. Ghidra's
defined-string inventory may omit short or packed Rust format data. The local
verifier reuses the pilot's Rust binary and P4 assembly, retains Evidence and
closes the session. It contacts no model API and needs no model credentials.

The coordinator selects the registered active condition from configuration.
Use `make benchmark` for all enabled models and cases or
`make benchmark-plan` to inspect scope. Frozen inputs and budgets remain tied to
existing cases. Build-only compiler/language-feature matrices remain unmeasured
by models until registered for inference. The framed native grader is used by
the coordinator; `make check-fixtures` validates the isolated grading controls.
Reports show old and new protocols separately. Readability
is a committed source-only assessment by the coordinating AI with model labels
removed; it is not an independent human panel.

## Exact returned-string framing amendment

The original grading drivers printed returned strings as bare lines. A saved
Rust submission produced extra line boundaries, shifting later comparisons.
The framed grading module verifies the unchanged candidates offline using numbered hex
frames. Embedded newlines remain part of the exact returned bytes; stray stdout
is attributed to its request. Native crashes remain failures and later missing
returns count as failed checks. Original batch-line grades are preserved and
are not the corrected primary partial scores. The historical report is unchanged.

Twelve independent controls cover correct returns, newline-containing returns,
stdout noise and crashes across C++11, C++14 and Rust, including both supported
empty-input encodings. Both models use the same amended driver, frozen vectors,
compiler modes, resource limits and isolated sandbox. Candidate source and model
inference are never changed or repeated. `grading-amendment-framed.json` records
the unchanged source hashes and old/new grading receipts. The report uses the
framed grades and exposes the original line matches as diagnostics.

The HTML report is `benchmarks/reports/stl-byte-inspection-20261019/index.html`.
Sources and hidden vectors are coordinator-only; evaluation agents receive only
their public target, prompt and manifest, never this README or authoring tree.

Inventory references:
- https://eel.is/c++draft/containers.general
- https://eel.is/c++draft/container.adaptors.general
