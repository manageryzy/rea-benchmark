# REA native-recovery benchmarks

Rebuild every report with one offline command from the workspace root:

    make reports

Open [the global HTML report](reports/index.html). All HTML, JSON, CSV, recovered
source exports, historical pages, compatibility redirects and the portable ZIP
are generated from saved records. Reports are never inputs to other reports.
No model API, credentials, compiler or target execution is needed to rebuild.

Run every enabled model against every active primary case, reuse validated inference,
grade saved answers, audit evidence and generate the **global** report with one command:

    make benchmark

Selection and scheduling live in [`config/benchmark.json`](config/benchmark.json).
`models: "all"` follows enabled entries in `config/models.json`; suites, experiments
and cases select registered frozen inputs. `condition: "active"` resolves each
suite's entry in `config/conditions.json`. The default covers six models × eleven
cases. Compiler/optimization expansion remains build-only and visibly unmeasured;
it is configured separately in `config/cpp-build-points.json`.

    make benchmark-plan
    make benchmark BENCHMARK_FLAGS=--offline
    make benchmark CONFIG=/path/to/selection.json
    make reports

The offline flag (or `REA_BENCHMARK_OFFLINE=1`) refuses a missing inference cache
before credentials or provider dispatch. Normal `make benchmark` explicitly starts
only unstarted configured cells. Corrupt/stale caches, interrupted attempts and
provider failures are preserved and reported, with no automatic retries.
`make reports` always builds all historical and active results, independent of the
execution selection, and never starts models. `make benchmark-run` is an alias.

Inference keys cover the model/reasoning, frozen binary, prompt, tools, manifest,
budgets and effective adapter hashes. Display names, prices, report code and
scheduling settings do not invalidate inference. Grade/source hashes and report
build IDs are separate. Earlier cache keys are validated using the reviewed
[`config/cache-compatibility.json`](config/cache-compatibility.json), the original
fingerprints, all committed artifacts and exact archived source profiles. Results
retain their recorded environment; cache reuse never relabels scores.

`make check-runner` executes the coordinator twice with provider dispatch blocked,
validates each saved cache, and checks one isolated simulated cache miss followed
by reuse. It also rejects corruption, changed model inputs and automatic retries.
The generated report publishes the validation receipt and input hashes. Historical
entrypoints and cohort-specific helpers are retained byte-for-byte under
[`archive/entrypoint-cleanup`](archive/entrypoint-cleanup/README.md); they are no
longer executable project entrypoints.

Generated diagrams include completion coverage, correctness, whole-program and
function-point recovery, stored readability, median runtime, case/model heatmaps, token mix,
cost ranges and the benchmark workflow. The overview can compare all recorded
native cases or only the cases graded by every model. Missing measurements stay
pending and partial usage is marked explicitly.
Saved request and tool timings explain runtime where those ledgers are available;
missing timings are not inferred from output length or token counts.
The Diagnostics page derives GLM reasoning, context growth, truncation and
answer-preservation measurements directly from cached wire traces. It includes
an implementation-review receipt, implemented evidence-delivery fixes and
remaining proposals. Saved reasoning text is counted and hashed, never
published. No installed ZCode runtime is needed to regenerate the diagnosis.

Results measure model + harness setups; model-only attribution is not certified.
The original-condition audit found DSH's default 12,500-estimated-token
spill policy in 21/22 primary runs, with the suggested read/grep recovery tools
blocked by the benchmark allowlist. Its 8,192-byte JSON logging cap is separate
from model input; no context-pressure pruning occurred in the audited sessions.
ZCode uses a different projection/cap. Scores remain unchanged and carry evidence
delivery flags in HTML, JSON and CSV, including the shared-case view.

Run `make audit-environment` to regenerate the read-only DSH audit receipt from
saved durable sessions (requires the local zstd decompressor). It starts no
models and requires experiments to be stopped. `make reports` only consumes and
validates the receipt against saved gateway evidence; it needs no client runtime,
session decompressor or credentials. Changed/missing inputs become unverified.

Current benchmark launches use `config/evidence-delivery.json`. Completed basic
runs retain profile `lossless-evidence-v1`; the eight harder cases use
`paged-evidence-v2` for every model, with `config/evidence-paging.json`.
DSH disables `spill-policy`, `tool-result-pruner`, and
automatic compaction. ZCode launches with a hash-checked, in-memory overlay in
`harnesses/zcode-evidence.cjs`: REA results retain full primary text and unique
structured fields, with proven text/structured duplicates removed. Other MCP
servers and the installed CLI bundle are unchanged. Provider context limits
still apply; an overflow must fail rather than trigger hidden shortening.
The common gateway retains returned originals verbatim, with SHA-256 references,
field catalogs and explicit `read_evidence` retrieval. Model-facing results and
pages are bounded to 24,000 Unicode characters. Full reassembly and both real
clients are checked offline with `make check-paging`; no provider calls are made.
The ZCode recorder treats the frozen output budget as a ceiling: the client can
lower `max_tokens` to fit complete input context, and each actual cap is recorded.
Setup, policy and provider failures stop before correctness grading rather than
becoming zero model scores. Original historical grading stays unchanged.

Run `make check-environment` to replay cached MCP results through the installed
DSH projection and ZCode serializer, followed by actual-client loopback transport
checks of large C++ and Rust/data payloads. It uses no provider API or credentials,
and does not open binaries, grade candidates or change old run artifacts.
`make check` includes this validation. `make reports` consumes its saved hashed
receipt without loading runtimes, executing subprocesses or using the network.
Future task identities include configuration and adapter hashes; old cache hits
remain labeled `legacy-recorded`, never upgraded to the fixed condition by replay.
The global report retains the original scores and their environment warnings.

## Structure

    benchmarks/
      config/                     suites, models, pricing, execution state, migration
      suites/
        record-parser/            basic C++20 / Rust / Go fixtures
        complex-native-v1/        optimized stripped multipoint fixtures
        stl-native-v2/            C++11/14 containers and data-tool protocol
      archive/                    historical entrypoints and one-time cohort helpers
      harnesses/
        deepseek-harness.mjs       model execution adapter
        zcode.py                   model execution and recording adapter
        zcode-evidence.cjs          benchmark-only, version-checked REA evidence overlay
      scripts/
        benchmark_config.py        selection and scheduling configuration
        cache.py                   inference identity and immutable-cache validation
        run.py                     one coordinator, cache selection and post-run grading
        records.py                 read-only registry and saved-record reader
        report.py                  one report generator
        report_charts.py           charts from saved records
        glm_diagnostics.py         read-only cached prompt/stream diagnostics
        audit_environment.py       durable DSH input audit and report qualification
        report_assets/             versioned HTML template, CSS and JavaScript
        check_structure.py         immutable artifact and source/import checks
        check_reports.py           two clean offline builds and link/ZIP checks
        check_environment.py       cached replay and real-client loopback checks
      runs/<experiment>/<model>/<case>/
      runs/<experiment>/conditions/<condition>/<model>/<case>/
      reports/
        index.html                 global comparison
        cases/                     unchanged source, point scores and comparisons
        history/                   earlier protocols, output-language baseline, diagnosis
        assets/
        data/
        benchmark-report.zip

Each suite owns its authoring sources, public targets, grading/build fixtures
and protocol-specific configuration. Harnesses execute tasks; they do not
define benchmark cases. The registry maps models and existing experiments to
those suites. There is no model-specific benchmark directory.

Historical run directories remain at their original paths. The existing
zcode-glm53-global-v1 directory is an imported raw-record location, mapped to
the same native-pilot, complex-pilot and STL/Rust cases. It is not another suite.
Raw receipts, candidates, evidence and frozen manifests remain unchanged.
The migration retains original code snapshots and records the path moves.

`config/conditions.json` registers the original and fixed delivery conditions.
The fixed condition reuses the same three suites and 11 frozen cases; it does
not introduce another benchmark definition. Its attempts are saved under the
existing experiment's `conditions/<condition>/` directory. Completed basic v1 attempts are reused. Three stopped v1 C++ delivery-repair attempts and
their observed costs remain diagnostics; the harder active attempts use v2. The global
overview shows current-condition charts, original/fixed tables and a paired
subset with equal coverage. All attempts remain in the case and expenditure
ledgers; the JSON exports distinguish `records` and `condition_records`.

## Older and lighter GPT baselines

`config/models.json` adds GPT-6 Luna (lighter) and GPT-5.6 Sol (older) on the
same DeepSeek Harness / Codex route as GPT-6.1 Sol, with high reasoning. They
reuse the existing cases, hidden tests, tools and budgets. Their first pilot
covers the three original-language record-parser cases; the remaining eight
cases per model use the existing complex/container suites. No new suite is created.

Use `make benchmark` for the complete configured comparison. To narrow selection,
set `models`, `suites` or `cases` in a configuration file and pass it with
`make benchmark CONFIG=path.json`. Preview with `make benchmark-plan CONFIG=path.json`.
The default report frequency is batch completion; set `execution.report_every`
for intermediate refreshes. These settings change scheduling, not model inputs.

Saved attempts are never retried automatically. Original and prior fixed
results keep their recorded source profiles; exact versions are exported with
the report. The GPT adapter now selects the requested model ID instead of a
fixed GPT-6.1 descriptor. Offline checks verify identical effective prompts,
tools and controls across GPT selections. `make check` includes these checks.

`config/pricing-gpt-baselines-20261020.json` freezes the new models' published
standard rates, long-context multipliers and unknown cache-write bounds.
Older does not imply cheaper: the frozen GPT-5.6 Sol rates exceed GPT-6.1 Sol.
All GPT costs are API equivalents for the existing Codex plan route; actual
subscription/credit debit remains unavailable. Original pricing is unchanged.

The global report defaults to shared graded cases, compares correctness,
readability, tokens, runtime and costs on matching coverage, and shows all
pending cells separately. `data/comparison.json` includes
`shared_case_comparison` as well as all-condition and historical totals.
Rebuild everything offline with `make reports`.

## Source checkout and result data

Git includes frozen input binaries and manifests intentionally: they define the
same compiler/optimization/ABI cases without relying on a local compiler rebuild.
It also includes the authored sources and independent oracles for inspection.
These references must remain outside each model's isolated analysis workspace.

Raw attempts, provider requests, reasoning, sessions, generated reports and local
execution state are ignored. `make setup` copies the stopped execution example
only when local state is absent; the report, plan and benchmark targets call it
automatically. It never overwrites an active run. A source-only checkout reports
unmeasured cells as pending. Reproducing historical measured results requires the
matching reviewed run dataset and its archived source profiles. Offline replay
and cache acceptance checks require that dataset and the documented local runtimes.

## Commands

C++ target compiler, compiler version, standard, optimization, library, string
ABI and strip mode are separate build axes. `config/cpp-build-points.json`
defines GCC 13.3 and Clang 20.1.2 at `-O0/-O2/-O3` on the same six C++ fixtures.
`make build-cpp-points` validates that grid against the existing independent
oracles, retaining exact frozen GCC `-O2` targets and reusing content-checked
compilation caches. The original basic GCC `-O1 -fno-inline` case remains
separate. No original source, binary, prompt or grade is overwritten.

New public inputs live in each existing suite's `public/build-points/`; private
build/validation records live in `private/build-points/`. This adds test points
within the existing suites, not another benchmark directory. The global
Compilers & optimization page distinguishes validated builds from actual model
measurements. Its six-point multipoint C++ pilot precedes a full paid grid.
The frozen primary cases remain unchanged. Newer C++20/23/26 grammar probes
record frontend availability separately, with zero model attempts; they do not
claim later-standard library-container recovery.

Clang's unnecessary constant-lambda-capture warning is retained as a warning
using the explicit container-only `-Wno-error=unused-lambda-capture` flag. All
sources remain byte-identical. Original C++ submissions continue to use the
common GCC grader; newer language cases need a compatible grader profile before
they can become model recovery tests.

    make reports
    make check
    make check-reports
    make check-environment
    make check-paging
    make audit-condition
    make build-cpp-points
    make clean-reports
    make reports

The default make target is reports. Checks verify preserved raw artifact
hashes, unchanged fixture bytes, source syntax/imports, execution-stop guards,
portable links and ZIP contents. Report reproduction performs two clean builds
with network and subprocess calls forbidden, then compares every file hash.
Recorded data timestamps and fixed ZIP metadata make unchanged inputs produce
byte-identical output.

Inspect the registry and cached attempts without model calls:

    bin/benchmark list
    bin/benchmark run --model glm-5.3 --harness zcode --dry-run
    bin/benchmark run --suite stl-native-v2 --model gpt-6.1-sol --dry-run
    bin/benchmark run --model all --condition lossless-evidence-v1 --dry-run

The model selector `all` now includes every registered model. Inspect its dry-run before selecting a paid scope:

    bin/benchmark run --model all --condition lossless-evidence-v1 --dry-run

One coordinator owns execution state and report writes. Each model runs cases
sequentially; up to three model queues run concurrently, with isolated target
workspaces and REA sessions. Runtime comparisons disclose the shared-machine
load. A harness/provider failure stops that model's remaining queue without
assigning correctness grades to unrun cases. Completed fixed-condition caches
must match effective inference inputs or an explicitly reviewed legacy binding;
interrupted attempts are not retried automatically.

Execution state is recorded in config/execution.json. Migration, builds and
report generation do not resume experiments. `make benchmark` is the explicit execution entrypoint; direct CLI live runs require
`--resume-experiments`. Configuration selects models and cases. Completed attempts are reused
after validation; interrupted inference is preserved and is never retried
automatically. Saved inference can be graded offline after execution is
explicitly resumed, without making the same model request again.

The shared coordinator generates the global report at batch completion, including
failed/incomplete batches. Set `execution.report_every` to a positive task count
for intermediate refreshes. Manual record/review updates
are incorporated by make reports. Missing measurements and reviews stay pending.

## Existing protocols

The primary comparison has 11 native protocol cells: three record-parser cases,
three optimized stripped C++/Rust/Go cases with eight tools, and five C++11/14
string-ABI/Rust cells with twelve tools. All configured models use the same registered cases. Its current completion coverage, saved grades and
preserved interruptions are generated in the global report; this document does
not maintain a second copy of those changing results.

Rust's eight-tool and twelve-tool conditions use the same target binary with
different frozen prompts and tool catalogs; they are distinct conditions.
Earlier C-output reconstructions and two C++ transport-budget attempts remain
separately labelled in history and the expenditure ledger.

Models see only the selected public ELF, prompt and restricted read-only REA
tools. Source, hidden vectors, other targets, execution, mutations, web, native
filesystem/shell tools and subagents are unavailable. Memory, skills and plugins
are disabled. The Ghidra gateway retains Evidence, bounds byte queries and closes
sessions. Local simulated-model validation is excluded from live usage scores.

Native graders compile unchanged candidates and execute them in bwrap without
network, credentials, reference sources or hidden-test files mounted. No compiler
or test feedback is returned to a model. STL/Rust data-tool scores use the saved
numbered byte-frame correction; earlier grading receipts are retained.

Readability uses stored source-only coordinating-AI reviews, scored separately
from correctness across five 0–2 dimensions. The generator never creates scores
or calls a model to fill a missing review.
`scripts/review_readability.py prepare` creates source files without model labels
or grade summaries; `apply --decisions FILE` attaches explicit five-part rubric
decisions. The coordinating reviewer has run context, so this is not an
independent blind panel. Missing or contract-rejected source stays unscored.

`scripts/audit_condition.py` checks completed fixed-condition outputs against
cached DSH sessions and ZCode HTTP tool messages, with no inference. The report
validates its receipt against raw-input hashes and keeps unaudited cells marked
unverified. The pinned Go 1.23.2 toolchain is resolved directly by the native
grader, including when the coordinator is started outside a login shell.

Published-rate pricing and exchange-rate snapshots are versioned under config
or each suite's config directory. Known usage from interrupted attempts remains
visible; missing usage can increase expenditure. GPT figures are API equivalents
for Codex usage. Actual invoice/prepaid/subscription debits are unavailable.

Planned expression-AST/cache families and unsupported C++23/26 types remain
explicitly unbuilt or excluded. They are never counted as executed coverage.
