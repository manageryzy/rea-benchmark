# Runtime and provider attestation

The [reviewed configuration](../benchmarks/config/runtime-attestation.json) defines
logical runtime components and sealed settings. New benchmark tasks resolve the
provider **before** calculating their inference action. This is a local content
receipt; it does not authenticate a remote service's implementation or model weights.

```sh
make attest-runtime       # Inventory installed components offline; no model calls
make check-attestation    # Synthetic mutation, route and privacy controls
make check-runner         # Existing observations reused twice; provider dispatch blocked
make reports              # Consume saved attestations and results; no installed tools needed
make pages                # Stage sanitized global HTML in docs/
```

`make benchmark` is still the execution entrypoint. Attestation is mandatory for
new dispatches, rather than another benchmark or an optional startup script.
The inventory command sends no requests and does not read credentials.

## Inputs and verification

Content inventories cover the REA package and installed dependencies, custom
Ghidra scripts, the Ghidra distribution, Java runtime/configuration, pinned Node
binaries, the selected DSH or ZCode installation, benchmark execution adapters,
native shared libraries found through trusted runtime ELF dependencies, launcher
utilities, Python and TLS trust files. Directory additions/removals, symlink
targets, file bytes and executable modes contribute. Java development/documentation
directories are explicitly excluded. Runtime dependency trees are conservative
supersets of loaded modules; changing an unused file inside a declared component
can therefore invalidate that runtime's key.
Native loader discovery covers the selected Linux x86-64/glibc artifacts;
packaged binaries for other operating systems, architectures or musl remain content-hashed but are
not probed with the host's loader. Python's system startup hook and installed
system packages are included, and unreviewed loaded Python dependencies fail closed.

Only reviewed component and system roots may be followed. The benchmark profile
check still accepts only its original `benchmarks/` source paths. A task cannot
add an arbitrary filesystem path to the runtime inventory. Source-code changes
to reports, tests, prices, scheduling and cache storage do not enter the runtime
manifest. The selected nonsecret environment is part of its identity.
The effective gateway and harness JSON are also frozen inputs. Their paths are
represented logically in the action, expanded only when materialized, and checked
with the public binary/prompt/tool/manifest bytes before and after dispatch.

The configured `verification_mode` is `startup_once`: each coordinator process
captures the selected harness runtime once, on its first preparation. Concurrent
DeepSeek queues share that admission; ZCode has its own runtime closure. Subsequent
preparations and pre/post-dispatch checks reuse the admitted content manifest.
This explicitly trusts the host environment after startup; it does **not** claim
to detect an installation changed during the batch. A process restart performs a
new capture; old on-disk receipts are never imported as current startup passes.
Task inputs, effective gateway/harness configuration and provider routes are still
checked at dispatch boundaries. The receipt records
`verification_method: startup_once_trusted_environment`.

Set `verification_mode` to `strict_per_task` to restore full content scans before
and after dispatch. Neither policy defends against a privileged host. Host kernel
and architecture labels are recorded, not cryptographically authenticated.

Focused offline checks: `make check-runtime-reuse`. Real mounted-image timings:
`make profile-runtime-reuse` (no model dispatch; local provider credential resolution
may require the normal environment). The probe records first admission separately
from warm preparation and verification, with a closed Chrome trace under
`.cache/benchmark-runtime/profiling/runtime-reuse-<timestamp>.trace.json`.

Measured on 2026-10-25 using the existing FUSE image, without model calls:
DSH startup admission 62.143 s, ZCode 19.790 s (two captures in total);
subsequent prepare 1.585–1.775 s, repeated prepare 0.877–1.092 s,
and individual pre/post checks 0.155–0.226 s. The 14 compact-manifest reuses
took 3.05 ms combined. The probe finished in 91.05 s including initial admission.
The closed 27 KB trace is `runtime-reuse-20261025.trace.json`; these measurements
are offline preparation/verification timings, not a new end-to-end model run.

The private full inventories and a stat-assisted SHA-256 memo live under
`.cache/benchmark-runtime/attestation/`. Metadata accelerates ordinary preparation
scans; action keys contain content digests, not timestamps. Startup-once admission
avoids subsequent tree scans and loader probes entirely. Only digest/count summaries and project source
hashes enter the saved offline inventory receipt. Report generation validates that
receipt against its attestor/configuration sources; it never scans installed tools.

## Provider routes and credentials

The action binds provider ID, model, wire protocol, normalized HTTPS base endpoint,
credential mode and source category. Credential values, OAuth account identities,
credential-file contents/hashes and token expiry are excluded. Rotating a credential
within the same mode/source does not invalidate inference. Changing from OpenAI API
to Codex OAuth, or changing the provider endpoint, creates a different action.
Launch rereads the selected source and refuses route changes rather than falling back.

* DeepSeek uses the official Anthropic Messages route. Its key is read from
  `DEEPSEEK_API_KEY` or the workspace DSH credential store.
* GPT follows the explicit provider selection in the runtime-attestation config.
  The authorized `openai-codex` condition uses the signed-in Codex OAuth grant
  with observed task limits and no provider response cap; it cannot silently
  switch to an API-key route. The auth-file locator follows `CODEX_HOME`,
  otherwise the user's Codex home. GPT preparation does not require DeepSeek credentials.
  See [request budgets](request-budgets.md) for the distinct limit and billing semantics.
* GLM standard/prepaid access uses the configured `GLM_BASE_URL` and
  `GLM_AUTH_TOKEN`, or the existing ZCode `zai-prepaid` provider entry.

DSH receives only credentials for its selected provider in its private temporary
credential file. TLS trust and REA/Java/Ghidra settings are pinned. Ambient proxy,
custom TLS and Node/library injection variables are not forwarded to model
runtimes. ZCode's upstream recorder disables proxies and redirects and uses the
recorded CA bundle. Changing these policies requires a configuration/code change
and a new identity; no account values belong in the versioned configuration.

This does not certify a provider-side generation cap. That is the separate R3
request-budget gate. [Candidate compiler/linker images](grading-runtimes.md) and
authenticated function returns are also separate from analysis-runtime attestation.

## Historical results

Old tasks, candidates, grades, usage and completion records remain byte-for-byte
unchanged. Missing past runtime/provider facts are **unknown**. An inventory of
today's installation is never backfilled into a historical task.

The [source upgrade bridge](../benchmarks/config/runtime-attestation-migration.json)
binds exact before/after adapter versions and archived old bytes. It permits only
read-only reuse of original, unattested observations. Prompt, binary, schema,
model, budget and other semantic dependencies must still match. A later adapter
edit is not covered. Old action digests are preserved and never aliased into the
new attested namespace. Historical cache hits explicitly report their reuse scope.

Global report pages show execution-attestation status separately from the saved
offline installation inventory. Public Pages output contains bounded summaries,
not full manifests, provider endpoints, credential locators, JSON or raw reasoning.

## Bounded runtime manifests

Large component file maps use runtime-manifest schema 2. The compact manifest
retains each component's content hash, file count, byte count and CAS digest;
small maps stay inline. The complete referenced map is a declared inference
Merkle input, not an external path or today's installation. Admission and report
verification reject missing or changed committed maps. Atomic CAS publication
shares repeated maps across models and cells. Historical schema-1 manifests and
results remain intact.

This fixes the diagnostic pilot's pre-dispatch size failure without raising the
32 MiB registration bound or dropping dependencies. A cached-real-inventory
preflight reduces its complete binding from 55,893,158 to 80,458 bytes and keeps
all four saved tasks below 45 KB. That validation made zero provider calls.
The 25 attestation controls include shared-map accounting, corruption/missing
map rejection and declared inference-Merkle-leaf checks.

## Per-task declared-entry context

When explicitly configured for a known-entry capability task, the attested
configuration contains a public-derived anonymous seed descriptor. The exact
`analysis.seeds` bytes enter inference as a Merkle input. The gateway receives
only the verified `REA_GHIDRA_SEED_FILE` path under that task's immutable input
root. Fresh configuration verification re-derives the descriptor from the public
ELF and manifest, compares the actual seed bytes, and refuses extra or changed
seeds. Localization and premises-only execution have no seed-file override.

The offline analysis-context gate stores the actual gateway observation and its
source-bound validation in separate AC/CAS actions. Its selected validation is a
pre-dispatch requirement. The analysis components and native dependency subset
must also match the participant's captured runtime. This gate launches no model
client and makes no provider request.

A budget stop can retain complete usage while lacking a successful inference
completion manifest. Saved pre/post runtime checks remain available; the strict
report certification stays unverified when its required completion evidence is
missing. Updating the reporter does not manufacture that historical evidence.


## Seeded ZCode startup and saved configuration

The nonfixture ZCode launcher passes the bound task input directory when deriving
its runtime environment. Seed verification occurs before allocating its local
recorder or starting a client. An installed-client regression uses an in-memory
provider response with loopback-only networking and verifies seed-path and
public-format delivery; it contacts no provider. This complements the separate
actual MCP/Ghidra supplied-entry control. It does not certify function access in
every case or remote model behavior.

Reports obtain opt-in analysis flags from the attested harness configuration,
not the older four-field runtime projection. That configuration must match the
original `configuration/effective.json` Merkle input. Changing task labels or
flags cannot reclassify an old observation into a new prompt condition.
