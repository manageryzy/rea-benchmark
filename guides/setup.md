# Setup and operation

[Benchmark overview](../README.md) · [Detailed benchmark protocol and configuration](../benchmarks/README.md)

For planned grading repairs and capability expansion, see the
[audit implementation plan](audit-implementation-plan.md). It distinguishes
verified defects, proposed changes and the acceptance gates for new measurements.

## Automatic WSL temporary execution

`make benchmark` and `bin/benchmark run` detect WSL workspaces on Windows-backed
mounts. The launcher uses `TemporaryDirectory` on the configured Linux temporary
filesystem and a private Bubblewrap mount namespace. REA, Ghidra, Node, DSH and
ZCode retain their normal absolute workspace paths inside that process; temporary
directory names never enter runtime manifests or action keys. The original
workspace paths and installed directories are not moved or replaced.

Configure `benchmarks/config/runtime-storage.json`: `mode` accepts `auto`, `off`
or `linux_temp`; `temporary_root` defaults to `/tmp`. Results, logs and action
caches remain persistent in the workspace. Only allowlisted public installed
runtime trees enter the pinned archive; credentials and benchmark traces do not.
The hashing SQLite memo also lives in the guarded Linux temporary directory.
Verified inventory receipts are copied back before cleanup; the disposable memo
does not replace the existing workspace database or become part of action hashes.
For workspaces outside `/tmp`, descendant tools also receive a private `/tmp`
mount, so their scratch files are removed with the runtime guard.
The namespace also receives a private standard `/dev`. A root bind alone leaves
device access restricted: opening `/dev/null` during nested MCP process setup
can fail with `EACCES`, which Node misleadingly reports against the executable.
The device/child-process regression uses null input/output streams. Installed
DSH/ZCode request controls run through the same temporary launcher, using local
fake providers; they therefore check this execution path before paid work.
The launcher source and storage configuration are declared runtime-attestation
inputs. Related changes invalidate runtime identity; random temporary names do not.

The first benchmark invocation packs installed runtimes automatically. To prepare
that dependency without model calls, run `make setup-local-runtime`. Later runs
verify and extract the cached archive instead of copying thousands of files from
the Windows mount. After upgrading installed tools, explicitly select a fresh
archive with `make setup-local-runtime RUNTIME_REFRESH=1`. Existing archives and
historical benchmark observations are retained. Installed source-directory edits
do not update an already pinned archive automatically.

The process guard forwards SIGINT/SIGTERM, waits for the child namespace and
removes extracted files on success, failure and interruption. SIGKILL and power
loss cannot execute a `finally` block; the next startup removes abandoned trees
using owner locks, without deleting a live run. Archive construction has a
separate lock and cleans interrupted packing directories. A missing namespace
facility fails before provider launch; there is no silent fallback to a slower
or differently attested runtime. Native Linux workspaces run directly by default.

Small benchmark selections prepare only their selected cases. The explicit
`make prepare-capabilities` command still inventories the full catalogue. Model
limits, historical grades and the exact-byte/action-cache checks remain unchanged.

## Warm verification and explicit cold regression

Run `make check` (or `make check-warm`, or `bin/benchmark check`) for the normal
warm-cache check. It has a default 180-second budget, configurable with
`make check WARM_BUDGET_SECONDS=240`. It verifies successful existing acceptance
receipts against current source/configuration hashes, recomputes the prepared
action keys for all frozen cases, checks their AC/CAS records and public/private
package bytes, checks committed native results, then freshly hashes the consulted
files again before reporting success. Shared toolchain admission happens once per
image. Missing/stale/corrupt caches fail; this command never builds fixtures,
grades candidates, inventories live runtimes, or calls a provider. It does not
replace fresh runtime/provider attestation before an actual model experiment.

`make check-cold` explicitly runs the full regression suite, including isolated
empty-cache builds, deliberate corruption, grading, client transports and report
reproduction. It can take much longer. Setup is timed as a separate stage;
every subsequent check command gets a saved duration and exit status. Individual
existing `check-*` targets remain available for focused development.

Warm stage timings and the optional cold command timings are saved locally in
`benchmarks/config/warm-cache-validation.json` and `cold-cache-timings.json`.
`make reports` regenerates the global diagnostics table and duration chart from
these receipts. Public output contains only allowlisted timings and counts;
dependency maps and saved candidate content remain private.

The warm verifier writes a content-addressed proof to the persistent workspace
`.cache/benchmark-actions/v3` on a miss and reuses it on subsequent invocations.
That cache and normal model/compile caches remain outside the runtime cleanup
guard. A real Bubblewrap control checks miss → create → cleanup → hit in a second
temporary runtime, with the same action key. Scratch directories are removed;
new cache entries survive. No random temporary pathname enters the proof key.

The first repair phase is available through `make check-measurement` and
`make regrade`. Regrading compiles and executes unchanged saved candidates in the
isolated grader, appends versioned grades/accounting, and rebuilds the global report.
It blocks network/model launches and preserves original inference and grade files.
The report's **Measurement validity** page compares original and selected grades.
Serialized request-budget guards and new-inference runtime attestation are
implemented separately; historical limits and execution identities remain unknown.
[Closed grading runtimes](grading-runtimes.md) provide the candidate compiler and
executor gate. The separate restricted C executor has passed its host-return
prototype controls. Localization, independent C and bounded type/layout/ABI
preparation now use the permanent typed coordinator. Capability inference requires an explicit diagnostic selection and a supported
request-budget route. Capability release remains pending independent validity
review and the remaining measurement gates.

Prepare and check the new family machinery without model calls:

```bash
make setup-capabilities
make build-capabilities             # valid target-build caches are reused
make check-capabilities             # artifact/localization controls only
make prepare-capabilities           # freeze named-artifact tasks without model calls
make check-structured-grading        # pinned Python JSON validation/scoring
make check-buffer-capabilities      # original-target vs host-owned C return/memory checks
make check-cpp-buffer               # original-language C++ pairing over the same inputs
make check-abi-probes                # artifact-driven original caller/callee probes
make check-pointer-probes           # pointer/object descriptions and original access probes
make check-variant-objects          # tagged variants, subobject offsets and original probes
make check-indirect-calls           # indirect calls, callback contexts, virtual slots and thunks
make check-optimized-locations      # real O3/LTO location diagnostics; zero model calls
make check-native-abi               # pinned Rust-native / Go-internal ABI preparation
make build-matched-baselines        # pinned Clang 20 / Rust / Go buffer originals
make check-matched-baselines        # independent original/C17/C++20 comparisons
make check-rust-buffer              # restricted Rust 2021 candidate controls
make check-go-buffer                # restricted Go ABIInternal candidate controls
make check-analysis-context         # public-entry seeds through actual MCP/Ghidra; no models
make check-capability-coordinator   # incremental per-check AC/CAS validation
make check-validation-cache         # focused offline cache/invalidation controls
```

See [incremental validation](validation-cache.md) for action dependencies,
initial cache population, selective checks and the explicit cold regression
target. Strict startup requires those receipts. The local default below keeps
runtime/provider identities while separating offline acceptance from execution.

### Prepared local startup

`benchmarks/config/benchmark.json` selects `execution.startup_mode: trusted_local`.
The launcher verifies and mounts the pinned runtime image; the coordinator trusts
that image for the process lifetime, hashes mutable adapters, and freezes the
complete selected denominator. Each model prepares and binds its next cell only.
It does not admit all 306 typed cells before the first native task. Offline
acceptance commands remain explicit and retain strict checks by default. This
mode does not claim that old validation receipts match newly edited sources.

Local preparation and compilation use separate worker processes sharing the
mounted runtime image. By default each model has one active measurement/API
session. `execution.max_parallel_models` caps the number of active models.
The opt-in experiment `execution.max_parallel_cases_per_model: 2` permits two
independent cases per model (default `1`); two models then use at most four
measurement workers. Each case retains its own serial request gate, evidence,
limits and attempt identity. A free slot takes the next case without retrying a
completed failure. Adaptive budgets freeze from completed answers available at
dispatch; another in-flight answer is not a budget sample.

A budget stop advances to the next case under `budget_stop_scope: case`.
A model-level hold or API failure prevents new cases for that model even if its
other in-flight case subsequently completes; already-running cases may finish.
Concurrency settings do not change per-case limits or provider rate quotas.
The full campaign also uses `transport_timeout_scope: case` for a verified ZCode
stream timeout whose attempted recovery was blocked before forwarding. This
preserves the ungraded failure and advances to the next case; quota, credentials
and unknown failures still hold the queue. See [request budgets](request-budgets.md).

Native attempts now use the same stable logical trial/action distinction as typed
tasks. Matching saved actions retain their original path and cache checks. A
changed action gets `actions/<action-hash>` beneath its original cell; a saved
failure for that exact action remains terminal. `selected-attempt.json` selects
the current observation for reports. Old observations stay in history and total
expenditure but do not duplicate the current-case denominator. No random run ID
is part of the inference key. `make check-native-attempts` tests this offline.

Prepare reusable compiler images after installing/updating the grading runtimes:

```bash
make prepare-grading-images
```

The verified extractions are keyed by image identity under
`~/.cache/rea/grading-images`. Candidate compilers and programs still run inside
closed bubblewrap mounts with private writable workspaces. Trusted local startup
does not recursively rehash these prepared compiler trees on every task; it
checks the saved image/root identity. Use `startup_mode: strict` when fresh tree
mutation checks and source-bound offline acceptance are required.

Measure through the normal launcher without model requests or changing the
stopped/running switch (choose a new output/trace path for each measurement):

```bash
make benchmark \
  BENCHMARK_FLAGS='--models deepseek-flash glm-5.3 --startup-check record_codec_c_recover --startup-output .cache/benchmark-runtime/startup-check-example' \
  TRACE=.cache/benchmark-runtime/startup-check-example.trace.json TRACE_FLAGS=--detail
```

This freezes the full configured selection in a temporary registry, prepares the
named real task, and compiles/executes a local control. The C-buffer check uses
its reviewed reference in the production freestanding compiler and authenticated
emulator; native checks use a labelled compiler control, not a model answer.
No inference result or model score is created. The command fails its timing
acceptance if compilation starts more than 30 seconds after launcher entry.
Compilation start, execution start and total timings are separate fields.
Temporary schedules/workspaces are removed; prepared compiler images are an
intentional persistent cache. `make check-startup-policy` runs focused offline
queue, budget, context, binding and cache-reuse controls, not the 104-check suite.

See the [capability suite](../benchmarks/suites/capabilities-v1/README.md) for its
explicit build condition, current scoring scope and remaining release gates.
The [typed capability protocol](capability-conditions.md) explains the independent
C17 condition, task/artifact contracts and cache boundaries.
The [matched-baseline guide](matched-baselines.md) defines the additional
compiler/language preparations and the restricted Rust/Go runtime boundaries.
These baselines are enrolled as separately configured compiler/language conditions.
Their current preparation and model-observation states come from the global report.
The [type/ABI guide](type-abi-conditions.md) defines the initial integer-record
scope, isolated T1/T2/T5 premises and remaining scientific-validity gates.
The [pointer/object guide](pointer-conditions.md) describes the separate T3 schema,
paired layouts, overlap checks and original-build admission.
The [tagged-variant/subobject guide](variant-object-conditions.md) describes T4's
strict schema, active-payload observations and separate source-bound controls.
The [indirect/object-call guide](indirect-call-conditions.md) describes T6's supplied
layouts/signatures, call-relationship artifact and complementary receiver probes.
The [optimized-location guide](optimized-localization.md) describes L6's separate
O3/LTO diagnostic builds, exact-instruction review and offline instance controls.
The [native language ABI guide](native-language-abi.md) describes T7's separate
Rust/Go originals, native/FFI distinction and description-driven boundary probes.

This guide contains environment requirements, client registration, startup,
credentials, benchmark commands, report reproduction and maintenance. Commands
using `/mnt/h/work/rea` and the Windows account path describe the reference WSL
installation; adapt and review those paths for another host.

## Public benchmark source

The source repository includes the coordinator, harness adapters, report generator,
reference programs, independent oracles, frozen case definitions and pinned ELF
inputs. Full private reports, raw model traces, reasoning, account state, credentials,
downloaded runtimes and compiler scratch files are excluded from Git.

After cloning, `make reports` creates stopped local execution state if needed and
generates reports offline. Without the private result dataset, the private record
view remains pending; public pages retain the checked-in sanitized summary cache.
Historical audit snapshots are not new scores. Public HTML under `docs/` is generated
locally and committed for branch-based Pages hosting.
Use `make benchmark-plan` to inspect the configured matrix without model calls.
After installing the pinned dependencies and configuring credentials locally,
`make benchmark` runs missing cells and reuses valid caches. It can incur provider
charges. Never put credentials in committed configuration or result bundles.

The existing measured report can be reproduced from this source plus its matching,
reviewed result dataset. Raw local data is not included in the source repository
and has not been approved for public distribution. No public raw-result bundle is
included; the root README records a dated aggregate snapshot from the local dataset.

This source snapshot retains the current Linux/WSL toolchain and account-path
assumptions. `environment-lock.json` records the pinned components; the client and
startup examples below describe that environment. Full installation automation and
path portability are not provided by this source snapshot. Third-party dependencies
keep their own licenses and are installed separately.

Original benchmark code and reference program sources are licensed under [WTFPL version 2](../LICENSE).
The license text is reproduced from the [official WTFPL site](https://www.wtfpl.net/about/).
Third-party components, including runtimes linked into frozen ELF fixtures, retain
their respective licenses; see [third-party notices](../THIRD_PARTY.md).

## Rebuild benchmark reports

For the HTML-only public site and local GitHub Pages deployment, see
[publication and privacy](publishing.md). The private report exports below remain
local and must not be uploaded to Pages.

Run `make reports` from this workspace to regenerate **all** HTML pages, JSON,
CSV, source exports and the portable report ZIP from saved records. It runs
offline, without API keys, model inference, compilation or experiment retries.
`make check` verifies warm cache integrity. `make check-public` checks public
privacy, links and byte-identical public rebuilds; `make check-reports` checks full
clean report reproduction. Those broader checks also belong to `make check-cold`.

Open [the global report](../benchmarks/reports/index.html). See
[benchmark structure and commands](../benchmarks/README.md). The report shows saved
execution status; paid runs require an explicit command and never start while
rebuilding reports.

## Reference environment

| Component | Version / location |
| --- | --- |
| Host | Ubuntu 24.04, WSL2, Linux x86-64 |
| REA | 6.7.0, pinned in `package-lock.json` |
| Ghidra | 12.1.4, `.tools/ghidra_12.1.4_PUBLIC` |
| Java | Existing OpenJDK 21.0.12.1, `/usr/lib/jvm/java-21-openjdk-amd64` |
| Node.js | 24.22.0 LTS, `.tools/node-v24.22.0-linux-x64` |
| DeepSeek Harness | 0.2.1-rc.2, `.tools/harness-0.2.1-rc.2` |
| ZCode | Official 3.16.1 source, Agent CLI 0.17.0, `.tools/zcode-3.16.1` |
| C++ compilers | GCC 13.3.0 (`/usr/bin/g++`) and Clang 20.1.2 (`/usr/bin/clang++-20`) |
| REA source reference | `upstream/rea`, shallow clone of the official repository |

Node and Ghidra archives were checked against their published SHA-256 values.
The installed REA and Harness package integrity values were compared with the
official npm registry. `environment-lock.json` records versions and sources.
The system Node installation is unchanged.

## Decompile an ELF

```bash
cd /mnt/h/work/rea
source /mnt/h/work/rea/env.sh
rea doctor --provider ghidra --json
rea analyze /absolute/path/to/program --json
rea function /absolute/path/to/program main --json
rea decompile /absolute/path/to/program main --json
```

`bin/rea` also works by absolute path without activating the shell environment.
Use Linux paths, including `/mnt/c/...` for files on Windows drives. Ghidra is
the default provider; `--provider ghidra` makes the choice explicit.

For the reproducible example:

```bash
/mnt/h/work/rea/bin/smoke-test
```

This compiles `examples/hello.c` to `examples/build/hello` and decompiles
`calculate_total` without executing the target. Results are saved under
`results/`, including `doctor.json`, `hello-decompiled.json`, and
`hello-decompiled.c`. Native projects are temporary; retain JSON Evidence or
use REA's `--snapshot /absolute/path/to/snapshot.json` when needed.

`bin/ghidra-headless` exposes Ghidra's standard `analyzeHeadless` interface for
standalone Ghidra scripts.

## Codex

The Windows Codex registration uses `wsl.exe --distribution Ubuntu-24.04` to
launch `/mnt/h/work/rea/bin/rea mcp`. Its reviewable configuration is
`config/codex-windows.toml`; `config/codex-linux.toml` is the equivalent for a
Codex client running directly inside Linux. Register only the fragment that
matches the client OS. An existing separate Ghidra MCP integration does not
use this REA environment; select the `rea` tools for this workflow.

The additive registration helper preserves other settings and backs up an
existing file before writing:

```bash
/usr/bin/python3 /mnt/h/work/rea/scripts/register-codex.py \
  /mnt/c/Users/mana/.codex/config.toml --platform windows --apply
```

Restart Codex after registration, then check `/mcp` for `rea`. MCP startup is
allowed 30 seconds and each tool call 450 seconds. REA's default Ghidra startup
allowance is 330 seconds. See the [official Codex MCP documentation](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

## DeepSeek Harness

```bash
/mnt/h/work/rea/bin/dsh web --no-open
```

Open the authenticated URL printed at startup. In the Web UI, select
`/mnt/h/work/rea` as the workspace. Open **Settings → Models** to enter a
DeepSeek API key, or add another provider/custom model API. Enter credentials
in Harness; keep them out of committed documents and MCP configuration.

Harness uses `.harness/` as `DSH_HOME` and `.harness-agents/` as its independent
skill root. Its REA registration is `.harness/cordis.patch.yml`, sourced from
`config/cordis.patch.yml`, with `toolCallTimeoutMs: 450000`. The matching REA
workflow is installed under `.harness-agents/skills/reverse-engineer-anything`.
The local directories containing sessions and credentials are ignored by Git.
The wrapper always starts in this workspace.

The previous 0.2.0-rc.2 runtime is retained in `.tools/harness` for rollback.
The upgrade preserves the existing `.harness` settings, credentials and sessions.

The model picker includes `deepseek-flash` and `gpt-6.1-sol` through the
`openai-codex` route. GPT uses a private copy of the existing Windows Codex
sign-in. To import a refreshed sign-in while preserving other Harness settings:

```bash
source /mnt/h/work/rea/env.sh
node /mnt/h/work/rea/scripts/configure-harness-models.mjs
```

The helper also supports an `OPENAI_API_KEY`, which selects the OpenAI Responses
API route. Start a fresh session when switching models for a comparison.

See [Harness model configuration](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.md)
and its [MCP client contract](https://github.com/deepseek-ai/deepseek-harness/blob/master/packages/mcp/mcp-client/README.md).

## ZCode with Z.ai / GLM

The official [ZCode](https://github.com/zai-org/ZCode) CLI and terminal UI are
installed locally. Start it with:

```bash
/mnt/h/work/rea/bin/zcode
```

This workspace uses the **standard API / prepaid balance** route with
OpenAI-compatible Chat Completions. The selected model is `glm-5.3`, with high
reasoning. ZCode uses `GLM_BASE_URL` and `GLM_AUTH_TOKEN` from the environment,
falling back to your WSL `~/.profile` when either variable is missing. Your
configured base URL is used as supplied. Import them with:

```bash
/mnt/h/work/rea/bin/zcode-api-key --from-env
```

The launcher refreshes these credentials on startup and preserves your selected
model and reasoning settings. Profile contents and the token are never printed.
For manual key entry using the default `https://api.z.ai/api/paas/v4` endpoint:

```bash
/mnt/h/work/rea/bin/zcode-api-key
```

The prompt hides the key. Both methods save the provider configuration with private file
permissions under `.zcode-state/.zcode/v2/provider_config.json`. Browser sign-in
is not required for this route. The helper preserves other providers and makes
no model request. Installation and local diagnostics do not validate your key,
balance or model access.

To inspect configuration without revealing the key:

```bash
/mnt/h/work/rea/bin/zcode-api-key --status
```

The endpoint and billing route follow the official
[ZCode API-key configuration guide](https://zcode.z.ai/en/docs/configuration).

The wrapper keeps provider credentials, sessions and logs under the ignored
`.zcode-state/` directory. Project configuration in `zcode.json` registers the
same REA/Ghidra MCP server with a 450-second timeout. Memory, subagents, hooks,
skills and optional plugins are disabled in this analysis workspace. This is an
interactive analysis configuration; a benchmark must additionally restrict the
tool catalog and source access in its own isolated public workspace.

The CLI was built from official source commit
`0a1fc56d279fd1001f78387bd81aec1c26767411`, using the project's Node 24.14.0
and pnpm 10.33.2. Its terminal UI, native Linux support and external dependencies
are retained alongside the CLI bundle. The existing REA and Harness runtimes
are unchanged. `environment-lock.json` records the installation; local
validation receipts are in `results/zcode-setup/`.

```bash
/mnt/h/work/rea/bin/zcode --version
/mnt/h/work/rea/bin/zcode doctor --json
```

ZCode is an adapter under benchmarks/harnesses, sharing the same suite registry,
coordinator, cache checks, graders and report generator as DeepSeek Harness.
The existing GLM records are mapped to their original benchmark cases without
moving or changing raw results. See [the benchmark guide](../benchmarks/README.md).

See the official [CLI guide](https://github.com/zai-org/ZCode/blob/main/apps/zcode-cli/docs/usage/README.md)
for configuration, headless output, login, isolation and usage recording.

## Compare models

The [benchmark guide](../benchmarks/README.md) documents the three existing suites:
record-parser, complex-native-v1 and stl-native-v2. Harness adapters execute
existing tasks; adding a model does not create a benchmark suite.

Run `make benchmark` to cover all enabled models and active cases, reuse validated
caches, grade and audit saved answers, and generate the global report. Configure
selection in `benchmarks/config/benchmark.json`; use
`make benchmark BENCHMARK_FLAGS=--offline` to require zero provider launches.

The active evidence condition is `compact-evidence-v3`, configured in
`benchmarks/config/conditions.json` and `evidence-paging-compact.json`. It covers
all three native suites, their compiler/optimization variants, and future typed
capability tasks. Typed configuration and the diagnostic smoke selector use the
same policy; their condition-dependent namespace changes without relabeling old
attempts. REA results
deliver one exact normalized view, prioritizing pseudocode even when it is short
or empty. The model-facing result does not repeat both raw and normalized copies.
Each projected result retains its original content hash, provenance, available
JSON-pointer views and explicit `read_evidence` access to the complete original,
including metadata and differing raw observations. Every page remains bounded
by 24,000 Unicode characters; omitted content is retrievable, not discarded.

Historical `lossless-evidence-v1` inline and `paged-evidence-v2` conditions retain
their definitions and saved results. The new condition and its policy/source
bytes are distinct inference inputs, so old answers are not relabeled as compact
evidence measurements. Focused offline replay reduced one cached basic GCC-O0
tool result from 319,601 to 7,092 delivered characters, and nine Luna short-helper
results from 208,520 to 37,315 characters. These are measured character counts,
not token estimates or new model scores. No model results have yet been produced
under the new condition. The focused controls are
`node --test benchmarks/scripts/check_compact_paging.mjs` and
`python3 benchmarks/scripts/check_evidence_policy.py`; they make no provider calls.

The offline command inspects the entire configured matrix, continuing past
missing, stale and interrupted cells, and regenerates the global report. It
returns nonzero when any selected result cannot be reused; that is an execution
status, not a correctness score. Historical attempts remain unchanged. Cases
without a saved result do not trigger preparation or installed-runtime scans.
Saved harness failures are rejected before runtime setup. Runtime extraction is
deferred until the selected work needs installed analysis tools. Initial SDK
installation remains `make setup-grading`; full regression acceptance remains
the explicit `make check-cold` target rather than a benchmark prerequisite.

Reports prefetch saved inputs concurrently, reuse bytes within the build and
freshly check content before publishing. Read-only filesystem queries are shared
within each collection/validation phase and checked again with fresh syscalls.
Rendering uses guarded Linux temporary directories; finished output is copied
to an atomic publication directory in the workspace. Inference, grading and
report caches remain in the persistent workspace. No temporary path enters an
inference action key. Cache-only batches do not repeat historical trace audits.

Stage timings are printed by the command and saved in
`benchmarks/config/benchmark-status.json`, `report-timings.json` and
`benchmark-command-timing.json`. The generated
[pipeline timing page](../benchmarks/site/timings.html) is linked from Diagnostics;
`make reports` reproduces that page from these saved numeric receipts.

The 2026-10-23 whole-matrix offline measurement took **258.78 seconds**, including
**197.739 seconds** for global report generation (previously 446.93 seconds).
All 984 selected cells were inspected, with zero provider calls: 925 missing
caches, 58 changed-input rejections and one preserved harness failure. There
were **zero current inference cache hits**, so this timing does not establish
performance for a fully populated, currently valid inference matrix. Old results
remain in the global report. The measurement followed one aborted diagnostic
and a 465.81-second completed run before the final metadata optimization; no
broad regression suite was run. Private timing logs are retained under
`results/performance-review-20261023/warm-path-final/`.

Use `make benchmark-plan` or `bin/benchmark list` to inspect
saved attempts and pending cells without inference. All report generation uses
`make reports`, with [global results](../benchmarks/reports/index.html),
[history](../benchmarks/reports/history/index.html), and
[grammar/container coverage](../benchmarks/reports/coverage.html).

`make benchmark` is the explicit execution command. Migration and report commands
never resume experiments. Direct CLI execution requires `--resume-experiments`;
interrupted attempts are preserved without automatic retries.

The clients use independent REA sessions against the selected target. Keep
source, hidden tests and prior model answers out of evaluation sessions.

## Verification

Verified on 2026-10-19 (JST): Ghidra prerequisite checks; decompilation of an
x86-64 Linux ELF through the CLI and the Windows-to-WSL MCP transport; the
complete 207-tool MCP catalog; and an actual Harness `binary_session` call
through its MCP client. Harness's Web UI returned HTTP 200 with an
authentication cookie. Reports are in `results/`. The Harness integration
test used a local model fixture. After upgrading Harness to 0.2.1-rc.2,
live DeepSeek Flash and GPT-6.1 Sol authentication was verified, and all six
C++/Rust/Go pilot runs passed 1,417/1,417 behavioral tests each. Isolation,
fresh-session, binary/prompt hash and credential-cleanup checks passed.
The model routes also appear in the composed Web profile. The follow-up
original-language pilot also passed all six C++20/Rust/Go compilations and all
1,417 tests per run, with matching input hashes and no model retries. Its report
separates observed token usage from estimated API costs and unknown cash billing.

## Maintenance

Activate `env.sh` before running npm. `npm ci --omit=optional --ignore-scripts`
restores REA from the workspace lockfile. Harness has a separate lockfile and
pinned native-script approvals in `.tools/harness-0.2.1-rc.2/`. Review version changes
before updating either package; keep the workflow instructions aligned with REA.
Provider startup and heap limits can be changed with
`REA_GHIDRA_STARTUP_TIMEOUT_MS` and `GHIDRA_HEADLESS_MAXMEM` before launching a
new client; also adjust each client's tool timeout for a longer startup limit.

The compatibility choices follow [REA's installation guide](https://github.com/morluto/rea/blob/main/docs/installation.md#ghidra):
Ghidra 12.1.x, a matching 64-bit full JDK, and a procps-compatible `ps` on Linux.

For action-key inputs, Merkle/CAS storage, migration and invariance checks, see [Action cache](action-cache.md).
For pinned candidate compiler/linker/library images and offline controls, see
[Closed candidate-grading runtimes](grading-runtimes.md).
For the separate freestanding C17 prototype and its offline acceptance gates,
see [Host-owned C returns](c-executor.md).

For serialized provider limits and dummy-client verification with `make check-requests`,
see [Provider request budgets](request-budgets.md).

## Reusable rootless tool environment

See [runtime-image.md](runtime-image.md) for the SquashFUSE/Bubblewrap dependencies,
`make runtime-setup`, offline `make runtime-smoke`, and precise legacy archive
cleanup. The normal `make benchmark` entrypoint selects FUSE automatically when
available and otherwise reuses the same image's extracted Linux cache.

### Optional readability reviews (disabled by default)

Normal `make benchmark` and `make reports` do not request AI readability reviews.
Correctness grading remains enabled; existing readability scores may still be
shown as historical reviewed evidence. Unreviewed code remains unreviewed.

Only after an explicit request, prepare source-only review inputs with
`make review-prepare`. To include previously reviewed sources, use
`make review-prepare REVIEW_FLAGS=--reassess`. Neither command invokes a model.
After separate explicit review decisions have been produced, apply them with
`make review-apply DECISIONS=/absolute/path/decisions.json`; explicit reassessment
adds `REVIEW_FLAGS=--reassess`. The prior selected review's exact bytes are retained
under `readability-history/<sha256>.json` before the new review becomes selected.
The source hash must still match; correctness results and model attempts are not
rerun or changed. Reporting then uses the selected review via `make reports`.

### Continue a campaign after an adapter repair

A benchmark configuration can exclude exact already-processed cells with
`"exclude_cells": [["deepseek-flash", "capabilities-v1", "task_id"]]`.
Entries are validated model/experiment/case triples; other models remain selected
for the same case. Invoke the normal `make benchmark CONFIG=...` entrypoint.
This supports resuming unfinished cells without paying to repeat old attempts
when a relevant adapter change correctly creates new inference identities.
Keep the excluded attempts and their receipts: they remain historical results
under their original condition, not cache hits under a changed adapter. Public
reports still collect the complete catalogue and retained results. Reattempting
a repaired failure requires explicit authorization and a genuinely changed action.
