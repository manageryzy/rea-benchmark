# REA Benchmark

This benchmark measures six model/client setups recovering program behavior from
frozen Linux binaries. Executable tests and structured artifact checks score
native C++/Rust/Go recovery, function localization, source reconstruction, and
type/layout/ABI recovery.

[**Read the report**](https://manageryzy.github.io/rea-benchmark/) ·
[简体中文](https://manageryzy.github.io/rea-benchmark/?lang=zh-CN) ·
[日本語](https://manageryzy.github.io/rea-benchmark/?lang=ja) ·
[Setup](guides/setup.md) · [WTFPL license](LICENSE)

## Results and conclusions

<!-- benchmark-results:start -->

**985/1,170 model × task cells have valid saved grades; 602 have full marks.** The other 383 graded cells are below full marks; 185 cells are ungraded.

Saved data as of **2026-10-28T10:33:41.197182+00:00**; source snapshot `f77f7ad57749`. These are saved measurements, not live queue status.

| Model | Client route | Graded / planned | Full marks | Below full marks | Ungraded | Mean correctness |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| DeepSeek V4.1 Flash 1017 | DeepSeek Harness | 182/195 | 98 | 84 | 13 | 66.5% |
| DeepSeek V4 Pro 0813 | DeepSeek Harness | 173/195 | 108 | 65 | 22 | 74.6% |
| GLM-5.3 | ZCode | 184/195 | 80 | 104 | 11 | 52.3% |
| GPT-5.6 Sol | Codex | 75/195 | 45 | 30 | 120 | 88.3% |
| GPT-6 Luna | Codex | 184/195 | 107 | 77 | 11 | 73.8% |
| GPT-6.1 Sol | Codex | 187/195 | 164 | 23 | 8 | 98.0% |

Mean correctness gives each graded task equal weight: `mean(passed checks / total checks) × 100`. Full marks require all checks for that task to pass. **Coverage, task mix and recorded conditions differ; this is not a matched-coverage ranking.**

| Capability category | Tasks per model | Graded / planned cells |
| --- | ---: | ---: |
| Native languages | 42 | 245/252 |
| Localization | 9 | 24/54 |
| Reconstruction | 104 | 507/624 |
| Types / ABI | 40 | 209/240 |

Valid cached grades count once per model and frozen task, including reusable earlier results. Original answers, grades and runtime identities are preserved. Ungraded states are not correctness zeros.

[Full results and capability radar](https://manageryzy.github.io/rea-benchmark/) · [Native comparison](https://manageryzy.github.io/rea-benchmark/comparison.html) · [Localization, reconstruction and type/ABI results](https://manageryzy.github.io/rea-benchmark/capabilities.html)

[Tokens and cost estimates](https://manageryzy.github.io/rea-benchmark/costs.html) use saved usage and frozen price snapshots. Missing usage leaves the total unknown. Codex estimates are API equivalents; subscription debit is unknown.

<!-- benchmark-results:end -->

The results measure **model + client + supplied tools + recorded task limits**.
They demonstrate performance on the configured tasks, not general success at
reverse engineering or recovery of the original source text. Use the capability
radar and per-category results to inspect strengths and gaps, and the native
comparison's shared-case view when comparing the same measured tasks.

Correctness is scored by deterministic graders, not an AI judge. AI readability
review is optional, disabled by default, and separate from correctness; stored
historical reviews do not fill missing correctness scores.

## What is measured

A **task** is one frozen target and output contract, including its selected
compiler/language/ABI variant. A **cell** is one model evaluated on one task.
The current catalogue contains **195 tasks × 6 models = 1,170 planned cells**.
These are not 195 unrelated programs: variants deliberately exercise related
program families under different conditions.

- **Native language recovery:** 42 tasks from the record-parser, complex-native,
  and STL/Rust-data suites. Eleven primary fixtures plus 31 distinct
  compiler/optimization variants cover C++11/14/20, Rust and Go, GCC 13 and
  Clang 20, optimization levels and libstdc++ string ABIs. Registered compiler
  variants are included in model measurement.
- **Localization, reconstruction and type/ABI:** 153 tasks across six families:
  record codecs, dispatch registries, numeric aggregation, variable-sized
  objects, callback policies and session state. They include optimized/LTO
  localization, matched source-recovery/code-generation controls, scalar and
  object layouts, pointers, tagged variants, indirect calls and calling conventions.
- **Unpacking:** a planned category with no configured tasks or measured scores.
  It contributes neither a score nor a denominator.

The report's [task catalogue](https://manageryzy.github.io/rea-benchmark/capability-inventory.html)
and [compiler coverage](https://manageryzy.github.io/rea-benchmark/compilers.html)
show the concrete variants. Selected container operations, type probes and ABI
boundaries do not establish exhaustive language or library compatibility.

## How to interpret the results

| Report item | Meaning |
| --- | --- |
| Graded / planned | Coverage of the configured cells, including valid reused results. It is not a pass rate. |
| Full marks | Every check for the task passed. A partial result remains below full marks even if many checks passed. |
| Mean correctness | Average of each graded task's passed/total fraction; tasks have equal weight. Ungraded cells are excluded. |
| Budget stopped | No complete answer was available under that attempt's spending/output policy. Preserved without a correctness score. |
| Time limit reached / not passed | The task reached its recorded deadline without an answer while API evidence showed normal progress. Scored zero under that recorded policy. |
| Format needs review | A localization artifact could not be interpreted safely. It is not proof of an incorrect location. |
| Execution failure, interruption or not started | A score is unavailable; these states are not model correctness zeros. |

A complete answer can be scored even if it crossed an observed budget threshold;
its budget outcome remains visible separately. Non-localization submissions
that fail compilation or strict artifact checks can receive zero task points.
Full marks mean the configured checks passed, not that all possible inputs or
source-level interpretations were proven equivalent.

Reports select one admitted result per model and frozen task. Reusable earlier
grades count alongside newer grades, without changing their original environment
identity. Different saved conditions and budget policies remain distinguishable.
Invalid, stale or mismatched task packages do not become current results simply
because an answer exists. See [scoring and comparison limits](https://manageryzy.github.io/rea-benchmark/methodology.html).

## Method

### 1. Freeze the task and isolate the model

Each task binds its binary, prompt, tool catalogue, expected output artifact and
limits. Models inspect the public x86-64 ELF through REA and headless Ghidra.
Depending on the task, they submit recovered source or a structured localization,
type, layout or ABI description. The declared hints are part of the task contract.

Reference sources, private truth, hidden test vectors, prior answers, arbitrary
shell access and grader feedback are outside the model's analysis workspace.
Source recovery and matched code-generation controls remain separate task types.

### 2. Execute through the recorded client route

| Models | Reader-facing route |
| --- | --- |
| DeepSeek Flash and V4 Pro | DeepSeek Harness |
| GPT-5.6 Sol, GPT-6 Luna and GPT-6.1 Sol | Codex, using the signed-in OAuth route |
| GLM-5.3 | ZCode |

All routes use the declared REA tool interface. Model-facing evidence is bounded
and retrievable by reference; the original evidence is retained. GPT results are
presented as Codex results even when historical provenance names an underlying
adapter implementation. Scores describe the complete setup rather than an
isolated model ranking.

### 3. Grade independently, offline

Native submissions are compiled unchanged in pinned grading environments and
checked against independent behavioral oracles. Reconstruction tasks compare
return values and memory effects. Type/layout/ABI tasks use structured artifacts
and probes across original caller/callee boundaries. Localization uses frozen
same-build target truth and explicit JSON type/range contracts.

The grader does not ask another model whether an answer looks correct. Exact,
lossless notation conversions can be regraded offline with an append-only receipt;
ambiguous localization artifacts remain under format review. Original answers and
grades are retained. See [grading runtimes](guides/grading-runtimes.md),
[localization contracts](guides/localization-json-contract.md), and the
[public methodology](https://manageryzy.github.io/rea-benchmark/methodology.html).

### 4. Keep execution limits and accounting explicit

Limits belong to the saved attempt. Recent adaptive runs remove turn/request-count
caps and use a 65,536-token response allowance. On Codex OAuth this is an observed
threshold, not a provider-enforced output cap. Basic record-parser tasks have no
aggregate token/cost limit; other tasks use twice the complete-answer mean for the
same model and task class, frozen when the task starts. No eligible sample means
no aggregate limit for the first task in that group.

The contract specifies whether that aggregate metric is reported tokens or an
estimated cost with separate fresh-input, cached-input and output rates. A reached
observed threshold stops the next request, so the final admitted response can
overshoot it. Wall-time and tool guards remain; the recorded reconstruction/type
deadline is 1,800 seconds. Historical attempts keep their earlier limits.

Budget stops advance to the next task. Genuine provider/account failures can hold
a model's queue. The recorded GPT transport policy permits bounded retries of
specific transient errors; it is not permission to repeat failed tasks indefinitely.
See [request budgets](guides/request-budgets.md) and the per-attempt report for the
actual policy, rather than applying today's defaults to historical measurements.

Known token totals include repeated context and cached input. Published-rate cost
estimates are not invoices, and missing usage remains unknown. Codex subscription
credit consumption is not measured by API-equivalent dollar estimates.

### 5. Reuse evidence without relabeling it

Inference, compilation, grading and report rendering have separate cache identities.
Changing report prose, translations, prices or scheduling does not by itself require
new inference. A saved result must still match the relevant frozen inputs and
artifact identities. Failed and interrupted attempts remain in history; a new
attempt needs the applicable execution policy and authorization.

Runtime images and mutable adapters are bound at launch. The prepared local mode
trusts the pinned environment during that process; strict validation is a separate
mode. Historical records with incomplete runtime identity stay explicitly unknown.
See [cache identities](guides/action-cache.md), [runtime identity](guides/runtime-attestation.md)
and [incremental validation](guides/validation-cache.md).

## Reproduce the report

**To view or regenerate the published results, no model account is needed.**
The repository includes a sanitized result summary and generated public HTML.
From a source checkout with Python 3.12, run:

```sh
make pages
```

This validates the saved summary, renders `benchmarks/site/index.html` and stages
the same public site under `docs/`. It makes no model calls and does not run the
benchmark validation suite. It does not fetch new results, commit or push.

On a machine with the matching private result dataset, refresh the summary and
all local reports from saved attempts with:

```sh
make reports
```

Raw historical results cannot be reconstructed from the sanitized public summary.
An incomplete source-only checkout retains the published cache instead of
replacing measured results with empty rows. The result timestamp describes the
saved data, not the time the HTML was regenerated.

## Run new measurements

Install and review the [local setup](guides/setup.md) first. Linux/WSL execution
uses Bubblewrap, `squashfs-tools` and `zstd`; optional SquashFUSE/fuse3 uses
`/dev/fuse` when available. Otherwise a verified extracted image is reused.
The rootless tool image pins REA, Ghidra, Node, Java, Python and compiler runtimes;
no Docker daemon or systemd container is needed. See [runtime image setup](guides/runtime-image.md).

Preview the selected tasks before starting paid work:

```sh
make benchmark-plan
make benchmark-plan BENCHMARK_FLAGS='--models deepseek-flash glm-5.3'
```

After preparing dependencies and configuring the relevant accounts, a deliberate
run can reuse valid caches and measure missing selected cells:

```sh
make benchmark BENCHMARK_FLAGS='--models deepseek-flash glm-5.3'
```

`make benchmark` selects all enabled models and tasks in
[`benchmarks/config/benchmark.json`](benchmarks/config/benchmark.json).
It can make real model requests. Use `CONFIG=path/to/selection.json` for a reviewed
selection and `BENCHMARK_FLAGS=--offline` to refuse missing inference caches.
Existing saved failures are preserved under the configured queue policy.

Normal warm verification is `make check`; broad cold regression is the explicit
`make check-cold` command. A report rebuild does not require rerunning all checks.
Focused checks and valid acceptance receipts can be reused when their inputs match.
See [warm and incremental verification](guides/setup.md#warm-verification-and-explicit-cold-regression).

## Report languages and publication

The HTML report supports **English, 简体中文 and 日本語**, including charts,
filters and accessibility text. Independent translations live in
`benchmarks/scripts/report_assets/locales/{en,zh-CN,ja}.json`. Missing translations
fall back visibly and produce warnings; they are not silently marked complete.
See [translation instructions](guides/report-i18n.md).

GitHub Pages serves the generated `docs/` tree. Public pages contain allowlisted
metrics and case identities. Raw answers, reasoning, provider messages, credentials,
sessions, runtime downloads and full private reports stay local. Engineering
diagnostics and installation/audit details are outside the reader-facing report.
See [publication and privacy](guides/publishing.md).

## Repository map

| Path | Purpose |
| --- | --- |
| `benchmarks/config/` | Model/task selection, frozen conditions, budgets and runtime policies |
| `benchmarks/suites/` | Authored fixtures, frozen targets and independent grading inputs |
| `benchmarks/harnesses/` | Client/provider adapters and evidence delivery |
| `benchmarks/scripts/` | Coordinator, graders, cache readers, report generation and focused checks |
| `benchmarks/public/summary.json` | Sanitized saved-result snapshot used to rebuild the public site |
| `docs/` | Generated GitHub Pages site |
| `guides/` | Setup, methodology details, runtime and maintenance instructions |
| `bin/rea` | Workspace launcher for the pinned REA/Ghidra environment |

The [benchmark guide](benchmarks/README.md) provides the extended command and
protocol reference. For current scope and measurements, use the configured
catalogue and the dated global report above.

## Related projects and license

| Project | Role |
| --- | --- |
| [REA](https://github.com/morluto/rea) | Binary-analysis CLI and MCP tools |
| [Ghidra](https://github.com/NationalSecurityAgency/ghidra) | Disassembly and decompilation backend |
| [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) | DeepSeek agent runtime and benchmark adapter infrastructure |
| [Codex](https://github.com/openai/codex) | Signed-in route used for the GPT comparison |
| [ZCode](https://github.com/zai-org/ZCode) | GLM agent runtime |

This repository supplies the task definitions, isolation, adapters, independent
graders and reports around those tools. The benchmark is licensed under
[WTFPL](LICENSE); third-party components retain their own licenses, listed in
[third-party notices](THIRD_PARTY.md).
