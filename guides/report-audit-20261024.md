# HTML report audit remediation

This records the response to the 26 consolidated findings in
[`REA_CONSOLIDATED_AUDIT_2026-10-23.md`](../results/html-review-20261023/REA_CONSOLIDATED_AUDIT_2026-10-23.md).
The audit's numerical examples describe its older snapshot. Checks against a new
report derive populations from its saved records rather than assuming the old
23 filtered rows or 110 total rows.

This is report-only work. It changes projection, presentation and offline report
acceptance. It does not launch models, run targets, revise grades, or discard old
attempts. A changed report build identity is distinct from an inference identity.

## Scope of the changes

The overview, capability outcomes and cost pages use shared presentation functions
in `benchmarks/scripts/report_views.py`. The public renderer receives only the
allowlisted projection. Full capability configuration is a separate searchable
inventory; saved attempts have stable sanitized detail pages.

Model selection uses a bounded set of declared groups: all configured models,
models with the greatest saved graded coverage, and eligible groups sharing a
harness. Each group offers its common graded cases and an explicitly unequal
per-model subset view. This is **not** an arbitrary model-subset editor.

## Finding map

“Implemented” below describes the code change, not universal accessibility or
cross-browser certification. Focused fixture and generated-page checks are listed
separately below. Remaining boundaries are stated in the final column.

| ID | Implementation and evidence location | Remaining acceptance boundary |
|---|---|---|
| A01 | Shared result-first overview; scope, current native coverage and typed history precede the comparison. `report_views.overview` | Opening-screen geometry is checked on generated Chromium pages; no participant comprehension study. |
| A02 | Capability outcomes first, actual saved-attempt rows and stable attempt pages; full configuration moved to a searchable paged inventory. `report_views.capabilities`, `Report.capability_inventory_page` | Complete inventory remains large as stored HTML, but is not the default results page. |
| A03 | Distinct labels for enabled configuration, registration, saved history and current graded cells; explicit cohort and grade denominators. `report_views`, `report_charts` | Historical observations do not form a funnel with current configuration. |
| A04 | Explicit bounded model groups, named case membership/exclusions and an empty-common-cohort message. `report_views.comparison` | Arbitrary model-subset selection is not implemented. |
| A05 | Check-weighted and case-equal results displayed together; per-case check weights retained in details. `Report.summary_table`, `report_views.comparison` | These descriptive weights do not create independent statistical trials or a new primary ranking. |
| A06 | Typed artifact rejection, semantic execution evidence and strict score are shown separately. `typed_outcome`, sanitized attempt pages | Missing execution evidence remains missing; historical strict scores are unchanged. |
| A07 | Native state definitions shared by validation, tables and completion charts, including budget exhaustion; unsupported chart states produce diagnostics. `report_states.py` | Current native saved data need not contain every fixture state. |
| A08 | Graded-attempt duration label and individual samples; cost page includes known native durations by terminal state, exact attempt values/links and the known ungraded exclusion count. `runtime_chart`, `report_views.costs` | Missing historical stages and typed durations are not reconstructed. Additional future runtime instrumentation is outside this HTML change. |
| A09 | Native, typed and combined known-cost subtotals with track reconciliation. `expenditure`, `cost_sum`, `costs` | Native rows are unique saved cell observations in the current dataset. An invoice is not available. |
| A10 | Sanitized saved prices, dates, rate rules, observed request periods and FX basis; rate uncertainty is separate from missing usage. `report_prices.py` | Unknown historical dates/coverage and actual billing stay unknown. Tiny positive amounts retain nonzero decimal or scientific notation in shared table/chart formatting. |
| A11 | Missing token categories remain null; explicit zero remains zero; charts show known-category sample coverage. `public_report.project_row`, `token_chart` | The 72 formerly zero-filled cache-write categories were checked against the current 103 native receipts; their saved cost bounds were unchanged. |
| A12 | Source rubric, five dimensions, coordinating-AI/non-blind limitations and available provenance shown near scores. `readability_note`, public projection | Existing reviews are not made independent by improved presentation; no new reviews are invented. |
| A13 | Experiment, case, output language, build identity and applicable limits are visible on case pages; model outcomes remain separate. `case_identity`, case renderers | Legacy metadata absence is explicit. Some detailed model-specific build/evidence content remains deliberately repeated. |
| A14 | Native result links retain model fragments; typed observations link to sanitized attempt pages and configuration/registration evidence. `case_table`, `heatmap`, `capabilities` | Public links expose only allowed summary evidence, not private requests or reasoning. |
| A15 | URL-backed search, structured fields and scope; consistent initial, pageshow and popstate handling; clear action and live result counts. `report_assets/report.js` | List filters explicitly do not change companion chart populations. |
| A16 | Safe malformed-fragment handling and expansion of disclosure ancestors. `revealLinkedEvidence` | Tests cover exceptional fragments without treating them as a security finding. |
| A17 | Responsive HTML capability cards, wrapping identifiers and compact mobile navigation. `report.css`, `capability_coverage` | Tested viewport geometry does not substitute for real-device touch testing. |
| A18 | Bounded named scroll regions, persistent headers/identity columns and wrapping heatmap labels. `report.css`, table/heatmap helpers | Chromium text bounds and scroll identity are tested; other browser engines are not certified. |
| A19 | Readable label sizes, constrained prose width, shared status/money helpers and page-specific context. `report.css`, shared view helpers | Local model rows retain configuration order; public rows use canonical JSON key order. Values agree by named identity, but order is not normalized across the two outputs and is not a ranking. Raw protocol identifiers remain in detailed evidence. |
| A20 | Explicit metric scopes, scales, units, exclusions, sample counts and exact data tables. `report_charts.py` | No confidence intervals, causal claims or efficiency rankings are inferred from these observations. |
| A21 | Program/point charts consolidated only for a structurally single-point cohort; original counts retained. `score_charts` | Multi-point cohorts keep distinct measures. |
| A22 | Keyboard-opened exact-value tables, skip navigation, current-page markers and named scroll regions. Assets and chart helpers | Actual screen-reader and real-touch testing remain unperformed. |
| A23 | Print selection context, hidden controls, repeatable table headings and deliberate selected-view disclosure handling. Assets | Export defaults to landscape; the acceptance check explicitly selects portrait for its alternate-layout PDF. This exports the full selected view, not a separate one-page executive summary. Representative checks below do not certify every page/export. |
| A24 | Shared reader-oriented overview, separate outcomes/inventory, cost and runtime scope, page-specific continuation links. `report_views`, page template | The ten detailed destinations remain available with clearer labels/order; the proposed six-category navigation consolidation is not fully implemented. |
| A25 | Shared outcome/overview/cost presentation behind explicit public allowlist; public field/schema controls retained. `report_views`, `public_report` | Equal-scope named rows, values, denominators and cost totals agree; display order still differs as noted in A19. Purposeful local-only source/evidence views remain separate. |
| A26 | Focused data/view/chart tests and persistent offline browser checks for interactions, layout, links and print. Check scripts below | Human comprehension, screen-reader, Safari/Firefox and real-phone acceptance remain open. |

## Reproduce checks without experiments

The capability diagrams page (`dimensions.html`) breaks saved native scores down
by source language, task family, behavioral handler, C++ dialect/string ABI,
compiler/optimization and readability criterion. Each axis lists its own sample
count and case identities. Correctness uses case-equal pass rates; readability
scales the stored 0–2 rubric to 0–100. Missing handler breakdowns remain
unmeasured even if the whole program failed. Radar polygons require every axis
to be measured; partial profiles show only observed dots. Each dimension group
uses one interactive overlaid radar with model checkboxes, all/none controls,
keyboard-accessible score readouts and URL-persisted selections. Visibility
selection never changes the scored cohort or the tables. No score is inferred
for unmeasured typed capabilities or compiler conditions.

`make pages` refreshes the public global report and local `/docs` directly from
the existing sanitized summary, without rereading private traces or launching
models. `make reports` additionally refreshes that summary and the private report.
The added `check_report_dimensions.py` has 16 focused checks and is included in
`make check-report-review`.

Use `make reports` to regenerate from cached results. The browser acceptance
command inspects an **already-generated** report and never invokes that build or
an experiment:

```sh
make check-report-browser
```

The underlying script accepts a site and artifact location, plus optional PDF
generation:

```sh
.tools/node-v24.22.0-linux-x64/bin/node benchmarks/scripts/check_report_browser.cjs \
  --site benchmarks/site --output .cache/benchmark-runtime/report-browser --pdf
```

Set `PLAYWRIGHT_MODULE` or `CHROMIUM_EXECUTABLE` when the installed browser runtime
is elsewhere. HTTP(S) resource requests are blocked and counted. Browser output,
screenshots and optional PDFs are private local acceptance artifacts, not public
report downloads.

The original `make check-report-review` selection contained 21 focused Python checks: nine
shared-view checks, eight chart checks and four public-projection checks. All 21
distinct checks passed across the initial run and targeted follow-up checks.
The capability-profile addition brings that selection to 37 checks; its 16 new
checks also passed independently. Run just its browser check with
`make check-report-browser BROWSER_FLAGS="--only 'capability dimension'"`.
These cover nullable
usage, prices, current-data accounting, states, weighting, cohorts and shared
rendering. They are separate from the
expensive `check_reports.py` two-clean-build regression and the 104 coordinator
checks. Neither broad cycle is needed for a browser-only check.

## Verification record

The initial fresh baseline reproduced the audit defects on the saved site:
at a 390px viewport, methodology was 615px wide, the header was approximately
501px tall, and capability cards scaled a 900-unit SVG into approximately 292px.

Independent recalculation from the saved 11-case native comparison reproduced the
audit's weighting example exactly (rounded here): DeepSeek Flash scored 76.2933%
check-weighted and 74.8665% case-equal; GLM scored 74.0124% check-weighted and
77.6701% case-equal. Both statistics use the same unchanged saved strict grades.
The ordering reversal is disclosed rather than resolved by selecting a preferred
formula.

Two normal `make reports` builds completed: the first took **330.234 seconds**.
Its browser check exposed a tablet filter-label overflow. After the focused CSS
repair and tiny-cost-formatting control, the final build took **346.318 seconds**.
The final public build is **`c33f2521fac0`**; the local private build is
**`796e674d3f5a`**. These are local generated artifacts; no push or deployment is
implied. The 104 coordinator checks and model experiments were not run.

The final **14/14 browser checks passed** in about **10.16 seconds** of measured
check time on Chromium **149.0.7827.55**. They cover history/restoration, combined
filters, scope/cohort URL state, malformed fragments, five viewport widths,
keyboard value access, 110 native model links across 27 detail pages, heatmap
identity/text bounds, named public/local metric reconciliation, print selection
and offline resource use. Browser script errors and external requests were zero.
The accepted receipt binds the index, styles, interactions and check-script hashes:

- [Accepted browser receipt](../.cache/benchmark-runtime/report-browser-accepted/acceptance.json)
- [Final public overview](../benchmarks/site/index.html)
- [Portrait PDF](../.cache/benchmark-runtime/report-browser-accepted/overview-a4-portrait.pdf)
- [Landscape PDF](../.cache/benchmark-runtime/report-browser-accepted/overview-a4-landscape.pdf)

The earlier failed tablet receipt and the later ordering comparison are preserved
in `report-browser/` and `report-browser-final/`. The final parity check compares
all named rows/cells, including duplicates, after ordering by identity. It records
the differing display orders explicitly; it does not hide a numerical difference.

| Reader-visible measurement | Earlier evidence | Final generated view |
|---|---:|---:|
| Public overview height at 1440px | About 8,710px in the original audit | 3,256px |
| Primary comparison start at 1440px | About 2,580px in the original audit | 901px |
| Capability page height at 1440px | About 130,350px in the original audit | 2,480px |
| Capability observations start at 1440px | About 129,149px in the original audit | 1,319px; 11 saved attempts |
| Main overview header height at 390px | About 501px, freshly reproduced before edits | 203px |
| Methodology document width at 390px | 615px, freshly reproduced before edits | 390px |
| Capability labels at 390px | About 4.2px effective SVG label size | 14px HTML labels |

All five sampled pages fit **320, 360, 390, 768 and 1440px** viewports without
document-wide overflow. The heatmap kept its model and case identities one pixel
inside the scroll region at desktop and mobile sizes, with no overflowing cell
text. These measurements compare layouts, not benchmark performance.

The PDFs have verified A4 orientations: portrait has 11 pages and landscape 15.
Representative visual inspection covered portrait pages 1/4 and landscape pages
1/4/5: scope/build context, values, columns and chart labels were readable, and
table headers repeated across page breaks. The remaining pages, other browser
engines, actual screen readers, real touch devices and participant comprehension
have not been certified. A passing DOM check is not independent task-validity
review or full accessibility acceptance.
