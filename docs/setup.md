# Setup and operation

[Benchmark overview](../README.md) · [Detailed benchmark protocol and configuration](../benchmarks/README.md)

This guide contains environment requirements, client registration, startup,
credentials, benchmark commands, report reproduction and maintenance. Commands
using `/mnt/h/work/rea` and the Windows account path describe the reference WSL
installation; adapt and review those paths for another host.

## Public benchmark source

The source repository includes the coordinator, harness adapters, report generator,
reference programs, independent oracles, frozen case definitions and pinned ELF
inputs. Generated HTML, raw model traces, reasoning, account state, credentials,
downloaded runtimes and compiler scratch files are excluded from Git.

After cloning, `make reports` creates stopped local execution state if needed and
generates the global report offline. With no separately supplied result dataset,
all measurements remain pending; historical audit snapshots are not new scores.
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

For the HTML-only public site and automated GitHub Pages deployment, see
[publication and privacy](publishing.md). The private report exports below remain
local and must not be uploaded to Pages.

Run `make reports` from this workspace to regenerate **all** HTML pages, JSON,
CSV, source exports and the portable report ZIP from saved records. It runs
offline, without API keys, model inference, compilation or experiment retries.
`make check` verifies cache preservation and byte-identical clean report builds
when the matching result dataset and local runtimes are available.

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
