# Public report and GitHub Pages

[Benchmark overview](../README.md) · [Setup and operation](setup.md)

The Pages site is the global comparison across all saved experiments and harness
conditions. It includes correctness, readability, runtime, tokens, estimated cost,
completion coverage, compiler coverage and aggregate diagnostics. Public case pages
contain scored metrics, not submissions or raw evidence.

## Rebuild from existing local caches

From the workspace containing the private cached attempts:

```bash
make reports
```

This rebuilds the complete local report, refreshes
`benchmarks/public/summary.json` through a field allowlist, verifies metric parity,
and builds `benchmarks/site/`. It makes no model calls. The local full report still
contains private exports; **only `benchmarks/site/` is suitable for Pages**.

The summary cache contains model/case identities, numeric usage, grades, rubric
scores, derived costs, compiler build statuses and audit counts. It excludes raw
requests/responses, credentials, provider errors, messages, answers, source text,
reasoning, logs and local paths. Its source report hash and cached-data timestamp
provide provenance without distributing the raw record set.

An incomplete or source-only checkout retains the existing public cache rather
than replacing measured results with pending rows. It can regenerate the public
site without the private dataset, credentials, installed harnesses or toolchains:

```bash
make check-public
make reports-public
```

Rendering blocks network access and child processes. The checks exercise that
guard, inject private canaries, reject unknown summary fields, validate local
links, and compare two byte-identical public builds. Only `.html`, `.css`, `.js`
and an empty `.nojekyll` marker may appear in the site. JSON, JSONL, CSV, raw source,
Markdown answers, SSE, archives and symlinks fail validation. The summary cache is
reviewable source input in Git; it is **not** copied into the published site.

## Automatic publication

The workflow in [`.github/workflows/pages.yml`](../.github/workflows/pages.yml)
runs on pushes to `master` and can be started with **Run workflow**. Pull requests
run the build/privacy checks without deploying. A push with updated sanitized
cache data regenerates the complete global site; it does not start experiments.

The build job has read-only repository access, checks out without persisting Git
credentials, and does not receive provider secrets. It uploads only the validated
site directory. A separate deployment job has the Pages and identity-token
permissions required by GitHub. There are no model-runtime installation steps,
inference commands, raw-result downloads or provider credentials in the workflow.

For the repository's one-time setup, open **Settings → Pages → Build and
deployment → Source**, and select **GitHub Actions**. Repository administration
access is required. After a successful deployment, the report is served at:

<https://manageryzy.github.io/rea-benchmark/>

Changes to the cached summary, generator or assets regenerate the site on the next
push. Raw local caches remain local; GitHub cannot observe new local experiments
until their sanitized summary is committed and pushed. Regeneration preserves the
cached-data timestamp instead of implying fresh measurements at deployment time.

GitHub's [custom Pages workflow guide](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
documents the build artifact and deployment permissions.
