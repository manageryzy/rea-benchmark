# REA Benchmark

A benchmark of agent-assisted recovery of readable C++, Rust and Go programs from
Linux binaries, measuring behavioral correctness, readability, token usage and
estimated cost.

[Setup and startup](guides/setup.md) · [Detailed protocol and configuration](benchmarks/README.md) · [WTFPL license](LICENSE)

## Methods

Models inspect frozen x86-64 ELF inputs through REA and headless Ghidra, then submit
source in the target's original language. The same per-case binary, prompt, tool
catalog and budgets apply across models. Reference sources, test vectors, previous
answers, shell access and compiler feedback are unavailable to the model.

| Suite | Cases | Main coverage |
| --- | ---: | --- |
| Record parser | 3 | Basic original-language C++20, Rust and Go recovery |
| Complex native | 3 | Optimized, stripped multipoint programs: containers, ranking, dependency scheduling, dispatch and ABI boundaries |
| STL and Rust data | 5 | C++11/14 containers across both libstdc++ string ABIs, plus Rust byte/string inspection |

The C++ fixtures cover all 16 C++11 container templates/adaptors, plus selected
operations on strings, `vector<bool>`, bitsets and valarrays. Grammar points include
variadic templates, moves, virtual dispatch, exceptions, generic lambdas and
C++14 captures. ABI cases include System V/Microsoft x64, Rust/native C and Go's
internal calling convention. These are selected behaviors, rather than exhaustive
coverage of every overload or binary-compatible library replacement.

The measured C++ inputs use GCC 13.3. GCC 13.3 and Clang 20.1, compiler version,
optimization (`-O0`, `-O2`, `-O3`) and string ABI define separate build points. The additional compiler/optimization
matrix is build-validated; its model inference is **not enabled** and is excluded
from the 11 measured protocol cases.

Correctness comes from compiling unchanged submissions and running independent
behavioral checks in an isolated environment. Whole-program success requires every
check to pass. Readability is a separate source-only coordinating-AI review across
five 0–2 dimensions; missing reviews stay unscored. This reviewer has run context
and is not an independent blind panel.

DeepSeek and GPT use DeepSeek Harness; GLM uses ZCode. All configured models use
high reasoning. Current delivery disables
DSH spilling, pruning and automatic compaction, fixes ZCode clipping, and exposes
explicit retrieval of complete saved evidence. Historical conditions remain
separate. The scores describe **model + harness setups**; model-only attribution is
not certified. Validated caches preserve the original inference and its recorded
environment, while reports are generated offline from saved records.

## Results summary

[Global HTML report](https://manageryzy.github.io/rea-benchmark/) · [Publication and privacy policy](guides/publishing.md)

Snapshot: **2026-10-20 15:21 JST**, report build `fd481cde4aa1`.
The active comparison has **58 graded cells out of 66 configured cells**
(six models × eleven cases). The table includes all active cases and discloses
unequal completion; it is not a ranking on matched coverage.

| Model | Graded / planned | Programs passing / graded | Behavioral checks passed / total | Readability / 10 | Reported tokens | Estimated USD |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| DeepSeek V4.1 Flash 1017 | 11/11 | 4/11 | 21,340/27,971 | 8.90 (n=10) | 24,473,622 | $0.65104 |
| DeepSeek V4 Pro 0813 | 3/11 | 3/3 | 4,251/4,251 | 7.50 (n=2) | 1,428,833 | ≥ $0.68557 |
| GPT-6.1 Sol | 11/11 | 10/11 | 26,681/27,971 | 9.09 (n=11) | 10,744,408 | $4.51048–$5.30051 |
| GLM-5.3 | 11/11 | 4/11 | 20,702/27,971 | 8.78 (n=9) | 24,369,808 | ≥ $12.06500 |
| GPT-6 Luna | 11/11 | 2/11 | 10,436/27,971 | 7.70 (n=10) | 5,145,223 | $0.16515–$0.19039 |
| GPT-5.6 Sol | 11/11 | 6/11 | 23,989/27,971 | 8.73 (n=11) | 11,875,765 | $12.39434–$14.05745 |

DeepSeek Pro completed the three basic cases, then stopped on a harness/provider
failure in complex C++; seven cases remain unstarted. That failure is not assigned
a model correctness score. Its three graded cases cannot establish performance on
the harder suite. On the three basic cases shared by all six models, Pro,
GPT-6.1 Sol, GLM-5.3 and GPT-5.6 Sol passed all three programs; Flash and GPT-6 Luna
passed two each.

Readability means include only reviewed sources, with `n` shown. Tokens aggregate
provider-reported usage across requests, including repeated context and cache
reads. Tokens and cost retain observed usage from failed active attempts; `≥`
marks incomplete usage. Costs use frozen published-rate snapshots. GPT figures
are API equivalents for the Codex route; actual invoice, prepaid and subscription
debits are unavailable. Cost ranges preserve unresolved billing assumptions.

The generated global report contains matching-case comparisons, per-case scores,
runtime charts, historical conditions and evidence-delivery diagnostics. See
[report reproduction](guides/setup.md#rebuild-benchmark-reports) for details.
Private reports and raw attempts are excluded from Git. Public HTML is generated
locally from a sanitized summary cache and committed under `docs/`. Replaying the underlying measurements
requires the matching private result dataset. This dated summary is not a
substitute for that dataset.

## Related projects

| Project | Role in this benchmark |
| --- | --- |
| [REA](https://github.com/morluto/rea) | Reverse-engineering CLI and MCP interface used to retrieve binary evidence |
| [Ghidra](https://github.com/NationalSecurityAgency/ghidra) | Disassembly and decompilation backend used by REA |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | Agent runtime used for the DeepSeek and GPT model routes |
| [ZCode](https://github.com/zai-org/ZCode) | Agent runtime used for the Z.ai / GLM model route |

The benchmark supplies frozen programs, isolation rules, harness adapters,
independent graders and reproducible reports around these projects. Third-party
components retain their own licenses; see [third-party notices](THIRD_PARTY.md).
