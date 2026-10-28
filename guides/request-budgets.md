# Provider request budgets

New inference freezes a versioned request contract when resolving its provider.
Both harnesses validate serialized JSON before forwarding. Historical results
without these receipts remain uncertified observations of their recorded conditions.

## Current adaptive policy (v9)

Contract v9 removes the 12-turn ceiling for every active adaptive case. Both
`max_model_turns` and `max_requests` are null; no tool-count-plus-overhead limit
is substituted as a hidden request cap. Tool calls and wall time remain separately
bounded. Old v8 and earlier receipts retain their original limits.

For new `lean-evidence-v4` attempts, `adaptive_budget` overrides the legacy
limits below. Provider-capped routes send **65,536 output tokens per response**,
even when the original case declared 8,192. Codex OAuth cannot send that cap;
it instead stops later requests if observed output in one response reaches
65,536. This is not a provider-enforced ceiling for Codex.

- Basic native (`record-parser`) and compiler variants: **no turn-count limit**, no
  cumulative token or cost cap.
- Other cases: **twice the same model/provider/type mean**. DeepSeek and GLM use
  component-priced cost (cached/fresh/output); routes without a frozen price
  use reported total tokens. There is no separate cumulative output cap.
- No eligible historical sample: the first case has no aggregate budget cap.
  The 64K response guard, wall-time/tool bounds and zero retries still
  apply. If no complete answer exists yet, the group remains in bootstrap.

A type consists of suite, capability kind, source language, output language and
reconstruction versus supplied-semantics code generation. Compiler variants
share a type. Only complete final answers with complete provider usage from
`lean-evidence-v4` contribute; correctness does not select samples. Incomplete
budget stops do not become artificially cheap samples. The latest complete
answer for each distinct case contributes once; the current case is excluded.
For example, prior same-type costs of $0.02 and $0.04 give a $0.06 threshold.

History is inventoried once per model worker, then its newly completed receipts
are ingested before the next case. Sample paths, content hashes and components
are frozen into that case's inference identity. They cannot change mid-answer.
Only safe counts, policy summaries and budget amounts enter the public report.
Changing the baseline is a new budget condition; old receipts keep their old
identity and are never relabelled as a new-condition cache hit.

The amount is `ceil(2 * sum(sample component cost) / sample count)` in integer
nano-currency units (or total tokens on unpriced routes). The check runs before
the next request, after full response settlement: the last admitted response can
overshoot. It is not an invoice hard cap. Missing billing components still block
further requests. A complete over-budget answer keeps a separate correctness
grade. Each model has at most one live API instance.

Run `make check-adaptive-budget` for focused offline history, stop-rule,
64K loopback proxy and public-projection checks. It makes zero model calls and
does not rerun the 104 coordinator checks.

## Execution boundary

DSH uses the workspace's loopback `provider-transport.py`. It checks the actual
model, protocol/path, generation ceiling, effective reasoning, declared function
tools, streaming setting and foreground execution before admitting HTTPS requests.
It does not add missing caps or switch providers. Forwarding ignores ambient
proxies, uses the recorded CA bundle and refuses redirects. ZCode uses the same
`request_contract.py` validator inside its recorder.

The configuration at `benchmarks/config/request-contract.json` distinguishes
provider-capped routes from the explicitly authorized Codex OAuth condition.
Legacy defaults, retained for old conditions and offline fixtures, are:

| Bound | Default |
| --- | ---: |
| Provider output tokens per response, capped routes only | 8,192 |
| Task output-token threshold | 32,768 |
| Model requests / turns, including empty and tool-free responses | 12 |
| Legacy reported input + output token threshold | 200,000 |

### Historical wallet-based DeepSeek / GLM conditions

`lean-evidence-v4` with `provider-request-contract-v5` or later replaces that raw-total
threshold for DeepSeek Flash and GLM-5.3 with a fixed-price cost threshold.
For v5–v7 the other limits above stay unchanged; v8/v9 override them as described above. Tool/time bounds and zero retries remain.
Configure `wallet_limits` in `benchmarks/config/request-contract.json`:

| Frozen route | Fresh / cached / output price per million tokens | Per-case stop threshold |
| --- | --- | ---: |
| DeepSeek official Flash | USD 0.30 / 0.006 / 1.20 (peak prices) | USD 0.041611514 |
| BigModel standard GLM-5.3 | CNY 8 / 2 / 28 | CNY 1.23672 |

Contract **v7** uses twice the mean component-priced consumption of the frozen
2026-10-26 05:55:32 UTC `lean-evidence-v4` sample: 19 complete-usage DeepSeek
attempts and 11 GLM attempts. The amount is
`ceil(2 * sum(component_tokens * nano_currency_rate) / sample_count)`.
Sample token components, count, timestamp, rounding and source-summary SHA-256
are recorded in each wallet's `limit_basis`; the baseline does not roll forward
while a campaign runs. Complete usage includes budget-stopped attempts and is
not evidence of normal completion cost for every case. Previous v5/v6 results
retain their original limits. Prices were checked on 2026-10-26 at
[DeepSeek](https://api-docs.deepseek.com/quick_start/pricing/) and
[BigModel](https://docs.bigmodel.cn/cn/guide/start/pricing).
The route, host, model, currency, integer nano-currency rates and threshold are
frozen in the inference identity. GLM's control uses CNY, without a currency
conversion. Changing display-only report prices does not alter inference keys;
changing enforcement prices does. Other routes retain their recorded policy.

Contract **v6** raises the DeepSeek/GLM response ceiling to **32,768 tokens**,
subject to the original case ceiling. The **32,768 total output tokens per
task**, 12 turns and time/tool guards stay unchanged. Wallet amounts are
configured independently as described above. The proxy
uses `min(client cap, remaining task output)` after validating the original
request. For example, after 10,000 generated tokens a new request may receive
22,768, rather than being refused because the whole 32,768 ceiling cannot fit.
This transformation is declared in the frozen contract and records both the
original and forwarded request hashes; it changes only the output cap.
At zero remaining output, the next request is refused. Provider-declared
truncation remains a terminal budget stop, with no automatic retry.
The legacy default ceiling remains 8,192 for other conditions/routes.

The ledger charges each request's latest cumulative usage once. Cache reads use
their cheaper price, and raw total tokens remain reported. The guard refuses the
next request after the cost threshold is reached; it lets the current response
settle. Consequently the last response may overshoot. This protection estimate
is **not a hard invoice cap**, and does not include earlier attempts or other
account activity. Missing final cost components stop further requests; they are
never treated as free. Complete over-budget answers retain a separate grade.

Lean evidence uses short case-local references and pointer-only catalogs.
Identical, fully delivered semantic views from different tools reference the
prior view. Hash lookup includes exact-content equality; partial views are never
suppressed. Explicit `read_evidence` always returns the requested original text.
Raw output and all original provenance remain retrievable using an empty pointer.
Old condition files and inference records remain unchanged.

Run `make check-wallet-evidence` for the focused offline guards, real loopback
proxy controls, paging reconstruction and public report projection checks. It
does not call models or rerun the 104-check coordinator suite. The optional
private replay covers 22 saved attempts: 854,462 to 762,404 tool-text characters
(10.77% fewer), four repeated full views replaced, 131 exact original-envelope
reassemblies. This is a fixed-tool-sequence character measurement, not a token,
cost or correctness improvement claim. Five historical raw-total stops were
below the new cost threshold; whether further turns succeed requires new results.

For provider-capped routes, the response ceiling is the smaller of the task's original ceiling and the policy ceiling. Before forwarding, the guard reserves the entire requested output cap.
Once a completed response reports its final output usage, unused allowance can be
reused. Missing usage retains the entire reservation. Cumulative stream updates
are counted once per request. A request that cannot fit is refused before any
provider forwarding. Legacy conditions do not rewrite caps; v6 wallet conditions
apply only the declared remaining-output adjustment above. No retries or partial
restarts are introduced.

The signed-in Codex OAuth route uses `observed_task_limits`. No provider
response cap is sent: `cap_field`, `cap_minimum` and `output_ceiling` are null,
and `cap_scope` is `none`. Turn, time and tool limits remain active. Output and
reported-total thresholds stop subsequent requests using observed usage; a single
response can exceed either token threshold. Missing final usage stops further
requests rather than inventing a reservation. This is a separate budget
condition and must not be represented as an 8,192-token response ceiling.

The reported-total threshold is a **stop-before-the-next-turn rule**. Input usage
is only known after forwarding, so the final admitted request may exceed it. It
is not a hard bound on total billed tokens or dollars. Cached input remains part
of this threshold, with its separate price semantics preserved in accounting.
Provider hidden-reasoning accounting remains route-specific.

Provider-declared response truncation is an explicit terminal limit outcome:
Anthropic `max_tokens`, Chat Completions `length`, and Responses
`incomplete_details.reason=max_output_tokens` become
`response_output_limit_reached`. The guard waits for complete transport and usage;
a disconnected stream is not relabeled as a token stop. The answer is incomplete,
so it cannot receive a correctness grade. A response can reach its cap while the
task remains below its cumulative thresholds (`budget_exceeded=false`). With
case-scoped execution the next case proceeds; no automatic continuation/retry of
the truncated case and no limit increase occurs. DSH's `max-tokens` exit no longer
creates a misleading generic harness-failure marker. Historical failure files
retain their original evidence and are not silently rewritten.

All routes use the same completed-response rule. Usage observations do not kill
an in-flight final answer. Once usage reaches the aggregate threshold, a further
request is refused before forwarding and the parent terminates the harness
instead of waiting for repeated local refusals. The coordinator applies the
configured `execution.budget_stop_scope`. An unfinished attempt retains the distinct `budget_exhausted`
status and does not receive a correctness grade.

If the admitted response finishes the task normally, the harness preserves it.
A response that crosses the threshold is recorded as `completed_over_budget`,
with the observed excess and a separate correctness grade. It is never counted
as budget-compliant. This requires a complete provider final response, complete
usage and normal harness exit; a tool-call continuation, truncated response or
transport error is not a completed final answer. Finishing exactly at the token
threshold is compliant but still records a budget-stop signal. With `model`
scope, the coordinator holds that model's queue, including on cache replay.
With `case` scope, it retains the budget outcome and continues to the next case;
it never retries the exhausted case. Offline inspection may inspect other saved
cases. Holds apply to the selected batch. A separately
authorized selection omitting the held cell can run other tasks, so this rule
does not establish an account-wide spending limit.

The full campaign in `benchmarks/config/benchmark.json` explicitly selects
`"budget_stop_scope": "case"`, `"transport_timeout_scope": "case"` and
`"skip_saved_failures": true`. Existing terminal failures remain in the denominator
and are not retried. The timeout setting covers one authenticated ZCode pattern:
a provider stream times out, then internal recovery is rejected by the concurrent
request gate before forwarding. The failed case keeps its harness-failure marker,
incomplete usage and no correctness grade; only the next case may run. It does not
enable retries or concurrent provider calls. Missing/ambiguous receipts, quota,
credential, runtime, integrity and other provider errors still stop that model's
queue. The timeout setting defaults to `model`. Other selections
default to `model` scope and do not skip saved failures. These scheduling settings
do not change inference identities or any per-case token, turn, tool or time limit.
A case-scope campaign generates its global report after its queues finish,
including budget stops or incomplete queues stopped by provider errors. The
command still returns nonzero for failures and preserves incomplete coverage.

No hidden grading result is supplied to a running model, and unsuccessful
attempts are not automatically retried. This policy does not restart paused
experiments or change historical cache namespaces, grades or bills. New
semantics and evidence delivery have new inference identities; old failures are
not silently reclassified or rerun.

## Loop protection and spending

The limits exist to bound runaway agents. Twelve provider requests bound a loop
even when it changes the conversation on each iteration; tool-call and wall-time
limits also remain active. Identical request retries are refused. The fixes to
evidence delivery reduce redundant input rather than increasing these limits.

Edit the positive values in `benchmarks/config/request-contract.json` to change
the limits for future runs. `max_model_turns` bounds reasoning/tool iterations,
`max_output_tokens` bounds aggregate generated output on capped routes (an
observed threshold on Codex OAuth), and `max_observed_total_tokens` stops later
requests after reported input plus output reaches the threshold. Cached input
still counts, so this is a conservative processing guard, not a dollar budget.

These are per-task limits. They do not establish a maximum campaign invoice:
many successful tasks can accumulate cost, and a final admitted response can
overshoot observed thresholds. Use the configured model/case selection for a
small pilot before authorizing a larger paid campaign. Existing attempts,
cache hits and recorded usage remain available without new model calls.

Old cached observations can still be rendered and compared under their recorded
conditions. They are not evidence that this new session policy was enforced.
The reviewed historical source bridge binds exact archived/current adapter
digests and excludes attested actions; it cannot alias an old observation into
a newly budgeted inference identity. Future equal-budget model comparisons need
matched policy/contract identities rather than relabeling the saved runs.

Other frozen limits are client
wall time (including harness startup), REA tool count, tool timeout and a provider
request limit of the smaller of twelve turns and tool count plus four (with the
turn ceiling configurable above). Automatic retries are zero: identical
payload retries and requests after transport failure are denied. Cancelling a
request at the deadline does not establish complete billing evidence.

## Protocol semantics

| Route | Required serialized cap | Reasoning setting |
| --- | --- | --- |
| DeepSeek Messages | `max_tokens` | Enabled thinking and `output_config.effort` |
| OpenAI Responses with API key | `max_output_tokens` | Request effort or effective GPT-6 `configuration_update` effort |
| GLM standard API | `max_tokens` | Enabled thinking and `reasoning_effort` |
| Signed-in Codex OAuth adapter | None; observed task limits only | Frozen effective effort; no provider fallback |

OpenAI's output ceiling includes visible and non-visible generated tokens,
including reasoning. GPT-6 configuration updates can select effort while keeping
request-level effort fixed for prefix caching. The guard validates that encoding,
rejecting inconsistent updates, pro mode and automatic truncation in this condition.
See [token counting](https://developers.openai.com/api/docs/guides/token-counting)
and [reasoning configuration](https://developers.openai.com/api/docs/guides/reasoning).

DeepSeek and GLM use their documented provider output-token caps; equal hidden
reasoning accounting across providers is not asserted. See
[DeepSeek Messages](https://api-docs.deepseek.com/guides/anthropic_api/) and
[GLM Chat Completion](https://docs.z.ai/api-reference/llm/chat-completion).

## Receipts and accounting

New attempts retain `request-budget.json`, request-linked cumulative usage and
separate harness output. Final `result.json` is written once after the recorder
closes, including on client failure. A failed task can have complete recorded
usage and remain ineligible for a correctness grade. Missing components stay
incomplete; a known token subtotal is not a complete invoice.

Receipts separate real provider transport attempts from local fixture forwards.
An admitted network attempt can have unknown delivery/billing after connection
failure. Zero attempts proves no transport forwarding; an attempt does not prove
a charge. `wire_enforced` proves local request checks, not independent verification
of a provider's generation implementation or remote model weights.
`observed_limits_enforced` identifies locally enforced observed task rules for
Codex OAuth and carries `provider_output_cap_enforced: false`. It does not certify
an omitted provider response cap. `completed_over_budget` retains that route's
cap metadata while separately disclosing the task excess and queue hold; it does
not certify compliance with the aggregate token threshold. Public native and typed summaries expose only
allowlisted limit metadata; raw requests, authentication and reasoning remain private.
Old records without an identifiable contract remain unknown. Shared-case radar
profiles match task coverage, not budgets, and list the recorded budget conditions.

GPT amounts remain API-equivalent estimates. The run uses the selected signed-in
Codex account; the actual subscription debit and invoice are not observable here.

The policy and implementation enter inference identity through the reviewed
runtime closure. New grading/cache admission requires a committed contract receipt.
Historical results retain their original action keys and missing budget evidence.

## Offline acceptance

For the focused evidence-delivery and budget-completion changes, use:

```bash
make check-harness-fixes
```

This runs cached/synthetic paging, policy-identity, request-gate, queue-hold and
report-label checks. It does not run the full coordinator suite, write a full
acceptance receipt, compile benchmark candidates, or call a model. These focused
passes do not replace any current source-bound admission required for a paid run.

The complete request-transport acceptance entrypoint remains:

```bash
make check-requests
```

Controls use dummy credentials and loopback responses, including installed DSH
DeepSeek/OpenAI adapters and ZCode. They cover continuations, streaming/JSON,
missing/wrong/oversized caps, uncapped OAuth observed limits, reasoning updates, retries,
deadlines, malformed requests, usage completeness and failure accounting. The
extended controls also exercise cumulative output reservations, missing/final
usage, cached-input totals, empty turns, both transports' stop receipts, the
installed ZCode termination path and stopping the model queue before another case.
A focused coordinator control verifies completed-answer holds, cache replay,
independent model queues and offline inspection with network access forbidden.
A complete successful suite saves a source-bound receipt at
`benchmarks/config/request-contract-validation.json`. It makes no model API calls
and does not rerun benchmark candidates.

### Streaming request handoff

Both live DSH and ZCode transports forward ordinary SSE frames immediately, but
hold terminal frames (including chat finish reasons and trailing usage) until
upstream EOF, successful response parsing, and ledger settlement. Only then does
the client receive completion and start its next request. This prevents a serial
client from being rejected while the prior handler is still releasing its slot.
Incomplete or malformed streams do not publish a success terminator. Genuine
overlapping requests and all existing token/turn limits remain enforced.

`make check-stream-handoff` checks both production handlers against local fake
upstreams, including delayed EOF, true concurrency, fragmented protocol frames,
usage settlement, JSON responses, and output-cap stops. It calls no model and
does not replace the full historical validation receipt.

### Deadline outcome (request contract v10)

A case that reaches its 1,800-second wall deadline without a complete final
answer is a completed evaluation with correctness zero (`deadline_exceeded`),
then the queue advances. Both DSH and ZCode require an authenticated local
request ledger: previous responses must have HTTP 200 and complete usage, and
an unfinished last response must have received HTTP 200 before local deadline
cancellation. Early disconnects, missing response headers, provider/auth/quota
errors, malformed responses, user cancellation and runtime-integrity failures
remain execution failures. A deadline certificate cannot admit partial source
or structured artifacts for positive correctness. Raw process exit codes,
partial answer text and incomplete last-response usage remain preserved.
Historical attempts retain their original classifications and policy IDs.

The gateway now follows REA's retained `inspect_analysis_view` continuation when
`analyze_function` exceeds REA's MCP response boundary. It validates the parent
evidence ID, selected binary hash, procedure and contiguous page coverage,
retains original errors and actual page envelopes in the private attempt, and
passes compact pseudocode pages to the model. It does not rerun analysis or
invent a full dossier; other retained facets are explicitly available through
bounded `read_evidence` views. Unknown continuations and mismatched evidence
remain errors.

Focused controls: `check-retained-recovery.mjs` uses the actual REA delivery
encoder/projector over MCP stdio with a synthetic oversized dossier;
`check_deadline_outcome.py` covers both production loopback transports, API
failures, interrupted usage and negative native/typed grading. Neither makes
model API calls.
