# REA Benchmark

A benchmark of agent-assisted recovery of readable C++, Rust and Go programs from
Linux binaries, measuring behavioral correctness, readability, token usage and
estimated cost.

[Setup and startup](guides/setup.md) · [Detailed protocol and configuration](benchmarks/README.md) · [WTFPL license](LICENSE)

## Methods

Models inspect frozen x86-64 ELF inputs through REA and headless Ghidra, then submit
source in the target's original language. Task configurations share the per-case
binary, prompt, tool catalog and configured budgets across models. Reference sources, test vectors, previous
answers, shell access and compiler feedback are unavailable to the model.

New inference binds the resolved provider route, effective settings and reviewed
runtime content before execution, with checks for changes before and after the
attempt. Historical runs without those records retain unknown runtime identity.
See the [attestation method and limits](guides/runtime-attestation.md).

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

Current offline grading preserves exact return bytes in numbered, length-checked
frames and validates Go imports using its syntax tree. Versioned regrading keeps
the original candidate and grades. Native frames do not authenticate return
provenance; historical provider budgets and full runtime identity remain uncertified.
Usage completeness requires request-linked evidence and is independent of task success.
New grades use [pinned compiler, linker, library and executor images](guides/grading-runtimes.md).
This improves grading identity without certifying historical inference or native return provenance.

DeepSeek and GPT use DeepSeek Harness; GLM uses ZCode. All configured models use
high reasoning. Current delivery disables
DSH spilling, pruning and automatic compaction, fixes ZCode clipping, and exposes
explicit retrieval of complete saved evidence. Historical conditions remain
separate. The scores describe **model + harness setups**; model-only attribution is
not certified. Validated caches preserve the original inference and its recorded
environment, while reports are generated offline from saved records.

The unreleased capability track separates function localization, independent C
reconstruction and type/layout/ABI recovery. Description-generated probes retain
an unchanged original caller or callee in each direction, including pointer
depth, buffer extents and overlap. Host-owned execution supplies return and memory
observations. These offline preparation controls add no model scores or spending;
the [implementation plan](guides/audit-implementation-plan.md) records the remaining
conditions, validity reviews and release gates. Future supported launches enforce
the [configured task token and turn limits](guides/request-budgets.md).

## Runtime dependencies

Linux/WSL needs **Bubblewrap**, **squashfs-tools**, **zstd**, and optionally
**SquashFUSE + fuse3** with access to `/dev/fuse`. The rootless tool image pins
REA, Ghidra, harnesses, Node, Java, Python and the GCC/Clang/Rust/Go compiler
environment. It uses FUSE when available and otherwise reuses an extracted cache;
per-run temporary files are cleaned separately. No systemd container or Docker
daemon is required. See [installation, image setup and cleanup](guides/runtime-image.md)
for commands, captured dependencies and the remaining host boundary.

## Results summary

[Global HTML report](https://manageryzy.github.io/rea-benchmark/) · [Publication and privacy policy](guides/publishing.md)

<!-- benchmark-results:start -->

Cached inference through **2026-10-27T19:00:43.694540+00:00**; report build `b3baad9ef5d6`.

**166/252** active cells have grades. Unequal completion is disclosed; this is not a matched-coverage ranking.

| Model | Graded / planned | Programs passing / graded | Checks passed / total | Readability / 10 | Known tokens | Observed USD estimate |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| DeepSeek V4.1 Flash 1017 | 32/42 | 1/32 | 52,432/93,158 | Unreviewed | 63,149,311 | $2.7923 |
| DeepSeek V4 Pro 0813 | 30/42 | 0/30 | 47,493/87,228 | Unreviewed | 68,495,234 | $12.1985 |
| GPT-6.1 Sol | 24/42 | 9/24 | 66,715/69,438 | Unreviewed | 17,688,006 | $7.8937–$9.1369 (rate range) |
| GLM-5.3 | 29/42 | 1/29 | 33,874/84,632 | Unreviewed | 50,206,948 | $26.3331 known subtotal; missing usage, final cost unknown |
| GPT-6 Luna | 22/42 | 0/22 | 14,863/63,262 | Unreviewed | 12,466,821 | $0.3782–$0.4317 (rate range) |
| GPT-5.6 Sol | 29/42 | 8/29 | 57,289/76,523 | Unreviewed | 18,217,689 | $22.9747–$25.7129 (rate range) |

Across all conditions, 328/328 saved grades use exact-byte grading; 0 differ in passed checks or program success from their previously selected grades. Offline regrading reuses the original model outputs and preserves historical grades.

Tokens include repeated context and cache reads. Costs cover observed usage at frozen published rates; missing request linkage or usage leaves the total unknown. GPT amounts are API equivalents, not measured subscription debits. Readability averages include only reviewed sources. Environment failures and unstarted cases receive no model correctness score.

[Scoring methods and scope](https://manageryzy.github.io/rea-benchmark/methodology.html) · [Report reproduction](guides/setup.md#rebuild-benchmark-reports). Raw traces, reasoning and private results remain local.

<!-- benchmark-results:end -->

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
