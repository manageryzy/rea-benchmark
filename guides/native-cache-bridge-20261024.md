# Native historical cache bridge review — 2026-10-24

The stopped native Rust selections had unchanged prompts, binaries, tool catalogues,
manifest bytes, model settings, budgets and runtime configuration. All five saved
observations were unattested. Report changes were absent from their inference
identities. The existing exact-source migration accepted the gateway and dispatcher
but rejected two subsequent adapter revisions.

The adapter versions below were read from the existing content-addressed store and
verified against SHA-256 before comparing their source. This is a code review of the
historical reuse boundary, not a runtime attestation or a new benchmark result.

| Adapter | Original archived SHA-256 | Previously reviewed SHA-256 | Newly reviewed SHA-256 |
|---|---|---|---|
| DSH | `f37a164d7fbc0ab0a4ff23cf77b96181f45c6de7e3d5d1826d0f69b731eb90f4` | `52d7a374818b8f7bdd83794d39bc636216f7e8d4f8e221b439f05cc7bdb0338c` | `e2190ab377d0a399721e848bdf39d590718da0c7ff5f20af151dca9720d3f575` |
| ZCode | `8af14da579338a4fdcf914954b271c53c8ee2a10f0436748da86a4cd7f92e82f` | `95949d235809d656d18d5d039f539fef25d9fcd6056efc03aced0a3bb96b6337` | `1a32ebb229208d824842fc462cf7e9f2465c37a742553277d1eb3762017f79c8` |
| Dispatcher | `440f8e53f13df1e57e816a515ff8c5d6052a49672afdb09dc84558318f8706f3` | `e4466427de7f578972af6b065109c7de095aab21e5f443a7ea1110f405fc6a0d` | `d4f632c80248680271cc351c959f6c346468dc53e9d37db4f3f58b5e8addb100` |
| DSH Codex guard | `f37a164d7fbc0ab0a4ff23cf77b96181f45c6de7e3d5d1826d0f69b731eb90f4` | `e2190ab377d0a399721e848bdf39d590718da0c7ff5f20af151dca9720d3f575` | `c67c774be215a481745cb9b4581776d2e977797295e41e619b66c9cdb0b9c021` |

DSH's complete follow-up diff adds `tools.length` guards: disable inherited
`rea-mcp` and omit its inserted connector when tools are empty. For the nine-tool
native Rust tasks the patch configuration is unchanged. ZCode's complete follow-up
diff moves `environment(...)` validation before allocating `Recorder` and passes
`input_directory=item['input_directory']`. That argument is consulted only when
`configuration.gateway.analysis_context` exists; ordinary native tasks do not have
that context. Successful unseeded startup retains the same sealed environment.

The dispatcher's sole follow-up change compares the saved transport status against
`request_contract.success_status(task['request_contract'])` instead of the constant
`wire_enforced`. This concerns newly dispatched, attested requests. The historical
bridge neither dispatches nor attests its observations. In particular, historical
GPT provider routes and response-cap enforcement remain unknown; these observations
must not be relabeled as new uncapped Codex measurements.

The final DSH follow-up adds one pre-dispatch guard for `openai-codex`: its task
contract must use `openai-codex-responses`, `observed_task_limits` and a null
`cap_field`. The other two added lines are comments; provider patch construction
does not change. The guarded route is unreachable for DeepSeek and GLM. Historical
GPT observations still pass only through the unattested reuse path, which never
executes this guard or claims their historical provider/cap identity. New Codex
tasks have attested actions and remain categorically ineligible for this bridge.

The bridge now explicitly accepts only `basic`, `complex`, `stl` and `core` native
tracks, exactly the four original public inputs, a nonempty target and tool catalogue,
and the original native identity/runtime schemas. Seeded, withheld, typed and attested
conditions are refused. Current source bytes still must match the exact reviewed
hashes, and original archived bytes must match their original hash. Changes to
prompts, binaries, tools, manifests, models, limits or runtime configuration continue
to change the action. No new attested alias or completion receipt is created.

An in-memory diagnostic reproduced the original action key for each of the five
stopped Rust observations (DeepSeek Flash, GPT-6.1 Sol, GPT-6 Luna, GPT-5.6 Sol and
GLM-5.3). It did not publish actions, edit old observations, grade candidates or call
providers. This review permits reuse only as historical unattested observations;
it does not certify current runtime equivalence or old provider budget enforcement.

Run the focused, synthetic offline controls with:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 benchmarks/scripts/check_native_cache_bridge.py
```

These controls use an automatically removed temporary directory and reject network
connections and action-cache writes. They exercise both native harnesses, zero-tool,
seeded/withheld/typed/attested refusals, genuine input changes, later adapter changes,
and corrupt original archives. They do not run the broad coordinator suite.
