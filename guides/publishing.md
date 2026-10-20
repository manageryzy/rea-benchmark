# Public report and GitHub Pages

[Benchmark overview](../README.md) · [Setup and operation](setup.md)

The global report is generated locally and committed to `master` under `docs/`.
GitHub Pages serves that directory. There is no repository-defined Actions
workflow, remote rendering job or scheduled model execution.

## Local regeneration

In a source checkout, rebuild from the sanitized cached summary and prepare the
publishing tree with:

```bash
make pages
```

This renders the report in `benchmarks/site/`, validates it, and copies the checked
HTML/CSS/JS into `docs/`. It needs no private raw dataset, model runtime, provider
credentials or downloaded toolchain. Rendering blocks network access and child
processes. The command does not commit, push or make model calls.

On the machine holding the private cached attempts, the normal report command
refreshes everything, including the summary cache and `docs/`:

```bash
make reports
```

The full local report remains under `benchmarks/reports/` and includes private
exports. It must not be used as the Pages publishing directory. An incomplete or
source-only checkout keeps the saved public summary rather than replacing
measured results with pending rows.

After reviewing updated results and generated files, commit and push the public
summary and `docs/` on `master`. Pages then publishes those static files. GitHub
cannot observe new local results until their sanitized cache and regenerated site
are pushed. Cached-data timestamps remain distinct from publication time.

## What is published

The site includes correctness, readability, runtime, tokens, estimated cost,
completion coverage, compiler coverage and aggregate diagnostics across all saved
experiments and harness conditions. Public case pages contain scored metrics,
not submissions or raw evidence.

`benchmarks/public/summary.json` contains allowlisted model/case identities,
numeric usage, grades, rubric scores, derived costs, compiler statuses and audit
counts. It excludes raw provider JSON, requests/responses, credentials, provider
errors, messages, answers, source text, reasoning, logs and local paths. Its source
report hash and cached-data timestamp provide provenance without distributing the
raw record set. This sanitized source cache is reviewable in Git and is not copied
into the Pages directory.

Only HTML, CSS, JavaScript and an empty `.nojekyll` marker are allowed in `docs/`.
JSON, JSONL, CSV, source exports, Markdown answers, SSE, archives and links to local
files fail validation. Authored setup and publishing instructions live separately
under `guides/`.

## Verification

```bash
make check-public
```

The checks inject private canaries, reject unknown cache fields and credential
patterns, exercise network/process guards, verify local links and compare two
byte-identical public builds. They also check that Pages staging preserves exactly
the validated site and refuses to overwrite non-site documents. Local cache
refresh verifies numeric metric parity with the complete private report.

## GitHub Pages setting

Use **Settings → Pages → Build and deployment → Source: Deploy from a branch**,
with branch **master** and folder **/docs**. Keep that setting when regenerating
locally; no provider keys or Actions secrets are required.

Report URL: <https://manageryzy.github.io/rea-benchmark/>
