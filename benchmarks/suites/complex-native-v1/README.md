# Optimized, stripped, original-language benchmark

This suite starts with three composite applications, one each in C++20, Rust
2021 and Go. A command registry routes six independent function points. Each
model reconstructs the entire registry in one fresh attempt. The first six
runs compare DeepSeek V4.1 Flash 1017 (`deepseek-flash`) and `gpt-6.1-sol` in
the same Harness 0.2.1-rc.2, REA 6.7.0 and Ghidra 12.1.4 environment.

Execution is currently stopped. Shared commands and the reproducible global report
are documented in [the benchmark guide](../../README.md). The following describes
the frozen historical protocol.

## Frozen pilot scope

| Point | Behavior under test | Source features and containers |
| --- | --- | --- |
| P0 | Text normalization, splitting and ordered counts | Strings/builders, ASCII classification, optional values, ordered maps or map plus sorted keys |
| P1 | Stateful ledger, insert/update/erase, signed quantities | C++ variant/visitor, Rust enum/match/Result, Go errors and switch; hash maps and ordered output |
| P2 | Stable ranking, duplicate identities, equal scores | Structs/tuples, vectors/slices, closures/comparators and stable sorting |
| P3 | Dependency scheduling, duplicate edges, cycles and ties | Adjacency vectors/slices, sets/maps, binary min heaps and deterministic traversal |
| P4 | Eight signed values and a 24-byte result | C++ System V and Microsoft x64 calls; Rust native aggregate and C scalar boundaries; Go ABIInternal |
| P5 | Runtime policy selection, thresholds and boundaries | C++ virtual classes and unique_ptr; Rust Box<dyn Trait>; Go interfaces with value/pointer receivers |

Original sources use clang-format 18.1.8, rustfmt and gofmt. Templates/generics,
descriptive names, helpers and useful comments are part of the authored code.
Grammar is source coverage; optimization can erase its recognizable form.
Behavior tests cannot prove that the model recovered the original container,
ownership design, class hierarchy or source spelling. Idiomatic equivalent
implementations are accepted and readability is evaluated separately.

## Builds, ABI scope and address hints

C++ uses -O2, Rust opt-level=2, and Go its default optimized build. No noinline,
disabled-inlining flag or LTO is used. Separate translation units/crates
preserve realistic ABI boundaries. Native function-pointer command registries
keep the six entry points reachable. C++/Rust use fixed-address ELF executables
for unambiguous Ghidra addresses; this is not an ASLR experiment.

Each optimized symbol-retaining binary is copied and processed with
`strip --strip-all`. Both copies must pass the independent oracle. Their .text
hashes must match; .symtab and .debug_info must be absent in the public copy.
Dynamic imports and Go runtime metadata remain where required by normal
executables. We do not claim that stripping removes all recoverable Go names.
Private symbols and disassembly verify actual emitted boundaries; Go inlines
its trivial pointer-receiver bridge, so that method is not counted as a second
surviving ABI routine.

Models receive the dispatcher/application-root address and each point's entry
address, plus record layouts and submission signature. This measures behavior
reconstruction with address hints; it does not score unguided entry discovery.
Names and algorithmic rules are not provided. ABI explanations need evidence
addresses. Reconstructed candidates use native safe code; this pilot evaluates
ABI-lowered behavior, not binary-link compatibility with a target library.

## Private checks and unchanged submissions

There are 512 checks per point, plus 16 invalid-dispatch checks: 3,088 per
application. Directed boundaries, fixed-seed random cases, and metamorphic
checks cover ASCII case, stable ties, erase behavior, graph edge duplication,
numeric signs, stack argument positions, policy thresholds and malformed data.
An independent Python oracle defines expected output. The complete input space
is not exhaustively tested and randomized cases may repeat inputs.

The grader requires one native-language code block from all visible assistant
text, preserving stream chunks and the separate final message. It compiles and
runs the candidate in bwrap without the project, credentials or network. It
returns no feedback to the model. No candidate edits, retries or repair loops
are allowed. Compiler errors, format failures, tool errors and timeouts remain
part of the first-attempt results. Positive, wrong-answer and wrong-language
controls validate all three compiler/grader paths before paid evaluation.

## Readability and comparison

The frozen rubric scores formatting, names, decomposition, language idioms and
explanatory comments from 0 to 2 each (10 total). Coordinating-analyst reviews
use source-only files with model identities hidden and include concrete notes.
This is subjective descriptive assessment, not independent human research or a
compiler-based fact. Formatter conformance and line-length statistics are
separate diagnostics; original answers are never reformatted for scoring.

Primary correctness is full-program success; point success and pass rates
explain partial recovery. Aggregate point scores weight each language/point
equally. Readability is not multiplied into correctness. Token columns retain
fresh input, cached input, cache writes, output and reasoning when reported.
All model inference and failed attempts count toward the token/cost total.

Pricing is verified from the providers before running. Per-step timestamps
determine DeepSeek peak/off-peak rates. GPT estimates account for long-context
rates and unknown cache writes as a range. Actual invoice debits and Codex
subscription-credit usage remain unknown; API-equivalent estimates are labelled.
Coordinating chat, setup, local Ghidra compute and future repeats are excluded.

## Isolation, limits and pilot gate

The same eight allowlisted read-only analysis/lifecycle tools are exposed to
both models. Source, filesystem, shell, network search, subagents, target
execution and prior answers are denied. The gateway accepts only the selected
public ELF and forces Ghidra. Each session has 60 calls, 30 minutes, and a
128,000-token common model ceiling, with provider retries disabled.
Models alternate order between languages. Provider caches are uncontrolled;
cache fractions and Ghidra/tool time are reported rather than hidden.

The six runs are the pilot only. The 36-run full suite remains unlaunched until
correctness, readability, usage, cost and harness reliability have been reviewed.
Additional programs are planned for recursive ASTs, LRU lists and hash maps,
deques, nested Result/Option, cleanup/RAII/drop/defer, UTF-8, mixed float/integer
ABIs, small/large aggregate returns and a separate LTO stress profile. These are
planned points, not claims of coverage in the current pilot.

## Layout and commands

`authoring/` contains readable originals. `private/` holds build maps, unstripped
copies, oracle vectors and preflight receipts. `public/<language>/<profile>/`
holds only a target, prompt and public manifest. Cohort outputs live under
`benchmarks/runs/`; HTML, machine-readable tables and audit results are under
`benchmarks/reports/`. Existing simple-pilot files remain untouched.

```sh
make build-fixtures
make check-fixtures
make verify-fixtures
/mnt/h/work/rea/bin/benchmark run --suite complex-native-v1 --model glm-5.3 --dry-run
make check
make reports
```

Official references:
- https://api-docs.deepseek.com/updates/
- https://api-docs.deepseek.com/quick_start/pricing/
- https://developers.openai.com/api/docs/models/gpt-6.1-sol
- https://gcc.gnu.org/onlinedocs/gcc/x86-Function-Attributes.html
- https://doc.rust-lang.org/reference/items/external-blocks.html
- https://go.dev/src/internal/abi/abi.go

## Transport diagnostics before the corrected pilot

The initial C++ pair is preserved in `benchmarks/runs/complex-pilot-20261019`.
DeepSeek enforced a 16,384-token response cap; the Codex adapter omitted its
configured cap. The pair is quarantined as setup diagnostics, and Rust/Go
requests were cancelled before launch. The corrected cohort requests 128,000
for DeepSeek, matching GPT-6.1 Sol's documented model output ceiling. The Codex
adapter still omits a wire cap; this limitation is disclosed.

The diagnostic also exposed the headless JSON projection's 8 KiB text cap. A
source-only observer now records every committed visible assistant message
without clipping, including code emitted before a closing tool call. No model
tool or prompt access is added. A 12,021-byte local fixture and capture
regressions verify this path without billing. The complete terminal snapshot
corrected GPT's diagnostic capture without a model retry or source edit. All
diagnostic token and cost receipts remain separate from the corrected six-run
pilot and are included in the report's overall observed expenditure.

## Completed pilot results

All six unchanged native-language submissions compile. DeepSeek passes
6,742/9,264 exact checks and 11/18 complete function points (one complete
program). GPT passes 8,143/9,264 checks and 15/18 points (two complete programs).
Both models pass all Go checks. The same six behaviors are compiled in three
languages, so these are 18 language/function points, not 18 independent
algorithms.

DeepSeek reports 4,412,260 tokens and a $0.32111 rate-based estimate; GPT reports
3,006,574 tokens and $1.54185–$1.82890 in API equivalents. Source-only readability
assessment by the coordinating AI, with model labels removed, averages 8.7/10
and 10.0/10 respectively. This is subjective, not a human panel.

Two separate C++ setup diagnostics add $0.70013–$0.80585 in estimates. Actual
invoice and Codex subscription-credit debits remain unavailable. The HTML
report includes all observed usage separately, code, decoded failure examples,
compiler receipts, pricing and protocol audit. The Rust compilation amendment
uses a proper library crate for valid inner documentation; both unchanged
Rust candidates were regraded, with no extra model calls.

Before full-suite expansion, review packed-constant visibility and the proposed
read-only byte/string tools. A tool-policy change needs a fresh cohort; the
existing pilot should not be mixed into that new matrix.

## Data inspection and STL expansion (after the frozen pilot)

The separate `config/pilot-data-tools-v2.json` now enables read_bytes,
search_bytes, list_strings and search_strings, with bounded read/search sizes
and the same selected-binary restrictions. `make verify-fixtures`
uses the existing Rust target and cached P4 dossier, preserves Evidence and
closes its session without a model call. Its verified receipt is cached by
content hash. The old eight-tool catalog and six pilot attempts are unchanged.

`../stl-native-v2/README.md` describes new strict C++11/14 fixture builds covering
all 16 container templates/adaptors, vector<bool> and related standard types,
plus optional native C++23/26 fixtures. The expanded 12-tool plan has 44 new,
unlaunched attempts; unsupported later-standard cases are not counted.
The historical 36-attempt plan above describes the original pilot protocol.
