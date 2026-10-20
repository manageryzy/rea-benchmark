"""Build portable HTML report pages and machine-readable comparison tables."""

import csv
import datetime
import html
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]
ROOT = BENCH.parent
sys.path.insert(0, str(BENCH / "scripts"))
from pricing import estimate, display

LANGUAGES = {"cpp": "C++20", "rust": "Rust 2021", "go": "Go"}
EXTENSIONS = {"cpp": "cpp", "rust": "rs", "go": "go"}


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def escape(value):
    return html.escape(str(value), quote=True)


def number(value):
    return f"{value:,}" if isinstance(value, int) else "—" if value is None else str(value)


def percent(group):
    return f"{100 * group['passed'] / group['total']:.1f}%" if group and group["total"] else "—"


def format_diagnostics(source, language):
    if not source.exists():
        return None
    code = source.read_text()
    lines = code.splitlines()
    command = {
        "cpp": [str(ROOT / ".tools/benchmark-format/clang_format/data/bin/clang-format"), "-style={BasedOnStyle: LLVM, IndentWidth: 4, ColumnLimit: 100}"],
        "rust": ["rustfmt", "--edition", "2021", "--emit", "stdout"],
        "go": ["gofmt"],
    }[language]
    result = subprocess.run(command, input=code, text=True, capture_output=True, timeout=20)
    return {"lines": len(lines), "nonblank_lines": sum(bool(line.strip()) for line in lines),
            "maximum_line_length": max(map(len, lines), default=0),
            "lines_over_100_columns": sum(len(line) > 100 for line in lines),
            "formatter": command[0].rsplit("/", 1)[-1], "formatter_check_ok": result.returncode == 0,
            "matches_formatter": result.returncode == 0 and result.stdout == code,
            "source_modified": False}


CSS = """
:root{color-scheme:light;--ink:#182a3c;--muted:#5e6b79;--line:#dce4eb;--blue:#215bc6;--teal:#007d76;--paper:#fff;--wash:#f3f6fa}
*{box-sizing:border-box}body{margin:0;background:var(--wash);color:var(--ink);font:16px/1.6 system-ui,-apple-system,Segoe UI,sans-serif}
header{background:#152b45;color:#fff;padding:34px max(24px,calc((100vw - 1240px)/2))}header p{color:#c3d2e4;max-width:850px;margin-bottom:0}
h1{font-size:clamp(28px,4vw,42px);line-height:1.2;margin:9px 0 14px;letter-spacing:-1px}h2{font-size:24px;margin:0 0 17px}h3{margin:0 0 8px;font-size:19px}p{margin:0 0 15px}
nav{display:flex;gap:20px;flex-wrap:wrap;font-size:14px}nav a{color:#d8e6fd;text-decoration:none}nav a[aria-current=page]{color:white;border-bottom:2px solid #83b0ff}
main{max-width:1288px;margin:auto;padding:28px 24px 60px}section,.card{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:25px;margin-bottom:22px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:22px}.grid .card{margin:0}.grid{margin-bottom:22px}.eyebrow{font-size:12px;font-weight:750;letter-spacing:1.6px;text-transform:uppercase}.muted,small{color:var(--muted)}.large{font-size:32px;font-weight:750;letter-spacing:-1px}.stat-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:20px}.stat-grid small{display:block}.badge{display:inline-block;border-radius:30px;background:#eaf0fa;padding:3px 10px;font-size:12px;font-weight:650}.ok{background:#dcf5ec;color:#066947}.partial{background:#fff1d8;color:#845200}.fail{background:#fee6e5;color:#94312d}.note{border-left:4px solid var(--blue);background:#edf3ff;padding:17px 20px;margin-bottom:22px;border-radius:0 9px 9px 0}a{color:var(--blue)}.scroll{overflow:auto}table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:13px 12px;border-bottom:1px solid var(--line);vertical-align:top}th{font-weight:650;color:var(--muted);background:#f8fafc}tr:last-child td{border-bottom:0}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.bar{height:15px;border-radius:4px;background:#e8edf2;display:flex;overflow:hidden;margin:9px 0}.bar span{display:block;min-width:0}.fresh{background:#285fc3}.cached{background:#88b5ef}.output{background:#009d8a}.legend{display:flex;gap:18px;font-size:12px;flex-wrap:wrap}.dot{display:inline-block;width:9px;height:9px;border-radius:2px;margin-right:5px}.foot{font-size:13px;color:var(--muted);margin-top:22px}button,select,input{font:inherit;border:1px solid #ccd6e1;border-radius:7px;background:white;padding:8px 12px;color:var(--ink)}.filters{display:flex;gap:12px;margin:0 0 18px;align-items:center;flex-wrap:wrap}pre{overflow:auto;background:#f5f8fc;border:1px solid var(--line);border-radius:9px;padding:18px;font:13px/1.65 ui-monospace,SFMono-Regular,Consolas,monospace;tab-size:4;max-height:760px}code{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.9em}pre code{font-size:inherit}details{margin:12px 0}summary{cursor:pointer;font-weight:600}ul,ol{padding-left:24px}li{margin:5px 0}.code-grid{display:grid;grid-template-columns:1fr;gap:12px}.point-title{min-width:180px}.meter{font-size:18px;font-weight:700}.pill-list{display:flex;gap:7px;flex-wrap:wrap}.pill-list span{font-size:12px;padding:3px 9px;border:1px solid var(--line);border-radius:20px}.pending{color:#8a6100}.narrow{max-width:950px}.nowrap{white-space:nowrap}@media(max-width:650px){main{padding:18px 12px}section,.card{padding:18px}.grid{grid-template-columns:1fr}header{padding:24px 18px}th,td{padding:10px 8px}.large{font-size:27px}}@media print{nav,.filters{display:none}body{background:white}section,.card{break-inside:avoid}pre{max-height:none}}
"""


def page(title, subtitle, body, current="overview", nested=False):
    prefix = "../" if nested else ""
    nav = "".join(f'<a href="{prefix}{path}" {"aria-current=page" if key == current else ""}>{label}</a>'
                  for key, path, label in [("overview", "index.html", "Overview"), ("coverage", "coverage.html", "Grammar & ABI"),
                                           ("methodology", "methodology.html", "Method & costs"),
                                           ("cpp", "cases/cpp.html", "C++"), ("rust", "cases/rust.html", "Rust"), ("go", "cases/go.html", "Go")])
    return f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{escape(title)} · REA benchmark</title><style>{CSS}</style><header><nav aria-label="Report pages">{nav}</nav><div class="eyebrow" style="margin-top:28px">REA / Native language recovery</div><h1>{escape(title)}</h1><p>{escape(subtitle)}</p></header><main>{body}<p class="foot">Local, portable report · Source answers preserved · Prices in USD · No external scripts or analytics</p></main></html>'


def table(headers, rows):
    return '<div class="scroll"><table><thead><tr>' + ''.join(f'<th>{escape(header)}</th>' for header in headers) + '</tr></thead><tbody>' + ''.join('<tr>' + ''.join(f'<td>{value}</td>' for value in row) + '</tr>' for row in rows) + '</tbody></table></div>'


def badge(value, good=None):
    color = "ok" if good is True else "fail" if good is False else "partial"
    return f'<span class="badge {color}">{escape(value)}</span>'


def outcome(row):
    receipt, grade = row['receipt'], row['grade']
    reason = receipt.get('turn_end')
    kind = reason.get('kind') if isinstance(reason, dict) else reason
    if receipt.get('timed_out'):
        return 'Time limit'
    if kind == 'max-tokens' and not row['code_file']:
        return 'Output limit / no submission'
    if not row['code_file']:
        return 'No native submission'
    if not grade.get('compiled'):
        return 'Compile error'
    if grade.get('program_success'):
        return 'Passed'
    return 'Partial behavioral recovery'


def main(run_id):
    cohort = BENCH / "runs" / run_id
    manifest = read(cohort / "run-manifest.json")
    config = manifest["configuration"]
    frozen = cohort / "pipeline/suite"
    prices = read(frozen / "config/pricing.json")
    destination = BENCH / "reports" / run_id
    for directory in [destination, destination / "cases", destination / "code", destination / "data"]:
        directory.mkdir(parents=True, exist_ok=True)
    reviews = read(cohort / "readability-reviews.json", {"reviews": {}})
    rows = []
    for language, model in config["run_order"]:
        directory = cohort / model / language
        receipt = read(directory / "result.json")
        if not receipt:
            continue
        grade = read(directory / "grade.json", {})
        source = directory / ("reconstructed." + EXTENSIONS[language])
        code_name = f"{language}-{model}.{EXTENSIONS[language]}"
        if source.exists():
            shutil.copy2(source, destination / "code" / code_name)
        row = {"model": model, "language": language, "receipt": receipt, "grade": grade,
               "cost": estimate(receipt, prices), "readability": reviews["reviews"].get(f"{model}/{language}"),
               "format": format_diagnostics(source, language), "code_file": code_name if source.exists() else None}
        row['outcome'] = outcome(row)
        rows.append(row)
    totals = {}
    for model in config["models"]:
        selected = [row for row in rows if row["model"] == model["id"]]
        total = {"model": model["id"], "label": model["label"], "attempts": len(selected),
                 "compiled": sum(row["grade"].get("compiled", False) for row in selected),
                 "program_successes": sum(row["grade"].get("program_success", False) for row in selected),
                 "passed": sum(row["grade"].get("passed", 0) for row in selected),
                 "tests": sum(row["grade"].get("total", 0) for row in selected),
                 "point_successes": sum(row["grade"].get("fully_correct_points", 0) for row in selected),
                 "point_count": 6 * len(selected), "usage": {},
                 "elapsed_seconds": sum(row["receipt"]["elapsed_seconds"] for row in selected),
                 "cost_low": sum(row["cost"]["low_usd"] or 0 for row in selected),
                 "cost_high": sum(row["cost"]["high_usd"] or 0 for row in selected),
                 "tool_calls": sum(row["receipt"]["tool_calls"] for row in selected)}
        for row in selected:
            for key, value in row["receipt"]["reported_usage"].items():
                if isinstance(value, (int, float)):
                    total["usage"][key] = total["usage"].get(key, 0) + value
        readability = [row["readability"]["total"] for row in selected if row["readability"]]
        total["readability_mean"] = sum(readability) / len(readability) if readability else None
        total["readability_reviews"] = len(readability)
        total['source_submissions'] = sum(bool(row['code_file']) for row in selected)
        total["cost_estimate_complete"] = all(row["cost"]["low_usd"] is not None for row in selected)
        if not total["cost_estimate_complete"]:
            total["known_cost_subtotal_low"] = total["cost_low"]
            total["known_cost_subtotal_high"] = total["cost_high"]
            total["cost_low"] = total["cost_high"] = None
        total["cost_per_success_low"] = total["cost_low"] / total["program_successes"] if total["program_successes"] and total["cost_low"] is not None else None
        total["cost_per_success_high"] = total["cost_high"] / total["program_successes"] if total["program_successes"] and total["cost_high"] is not None else None
        totals[model["id"]] = total
    audit = read(destination / "protocol-audit.json")
    report = {"cohort": run_id, "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "configuration": config, "full_suite_started": False, "totals": totals, "runs": rows,
              "audit": audit, "actual_billed_usd": None, "prices": prices}
    (destination / "data/comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    with (destination / "data/comparison.csv").open("w", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["model", "language", "compiled", "passed", "tests", "complete_points", "program_success", "readability_10",
                         "fresh_input", "cached_input", "cache_writes", "output", "reasoning", "reported_total", "estimated_usd_low", "estimated_usd_high", "actual_billed_usd"])
        for row in rows:
            usage, grade = row["receipt"]["reported_usage"], row["grade"]
            writer.writerow([row["model"], row["language"], grade.get("compiled"), grade.get("passed"), grade.get("total"), grade.get("fully_correct_points"),
                             grade.get("program_success"), row["readability"]["total"] if row["readability"] else "",
                             *[usage.get(key, "") for key in ["inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens", "reasoningTokens", "totalTokens"]],
                             row["cost"]["low_usd"], row["cost"]["high_usd"], ""])
    cards = []
    for total in totals.values():
        usage = total["usage"]
        readability = f"{total['readability_mean']:.1f}/10" if total["readability_mean"] is not None else "Pending" if total['source_submissions'] else "Unavailable"
        cards.append(f'<article class="card"><div class="eyebrow">{escape(total["model"])}</div><h2>{escape(total["label"])}</h2>'
                     f'<span class="badge">{total["attempts"]}/3 attempts recorded</span><div class="stat-grid">'
                     f'<div><small>Complete programs</small><div class="large">{total["program_successes"]}/{total["attempts"]}</div></div>'
                     f'<div><small>Complete function points</small><div class="large">{total["point_successes"]}/{total["point_count"]}</div></div>'
                     f'<div><small>Behavior checks</small><b>{number(total["passed"])} / {number(total["tests"])}</b></div>'
                     f'<div><small>Readability · analyst rubric</small><b>{readability}</b><small>{total["readability_reviews"]} reviews / {total["attempts"]} attempts</small></div>'
                     f'<div><small>Reported tokens, including cache</small><b>{number(usage.get("totalTokens"))}</b></div>'
                     f'<div><small>Estimated API equivalent</small><b>{display(total["cost_low"], total["cost_high"])}</b></div></div></article>')
    comparison = []
    for row in rows:
        grade, usage = row["grade"], row["receipt"]["reported_usage"]
        comparison.append([f'<a href="cases/{row["language"]}.html">{LANGUAGES[row["language"]]}</a>', escape(row["model"]),
                           badge(row['outcome'], bool(grade.get("program_success")) if grade.get('compiled') or not row['code_file'] else False),
                           f'{number(grade.get("passed"))}/{number(grade.get("total"))}',
                           f'{grade.get("fully_correct_points", 0)}/6',
                           str(row["readability"]["total"]) + "/10" if row["readability"] else "Pending" if row['code_file'] else "Unavailable",
                           number(usage.get("totalTokens")), display(row["cost"]["low_usd"], row["cost"]["high_usd"])])
    point_rows = []
    for language in config["languages"]:
        for point in config["points"]:
            values = [LANGUAGES[language], escape(point["id"] + " · " + point["title"])]
            for model in config["models"]:
                run = next((row for row in rows if row["language"] == language and row["model"] == model["id"]), None)
                group = run["grade"].get("points", {}).get(point["id"]) if run else None
                values.append(badge(f'{group["passed"]}/{group["total"]} · {percent(group)}', group["passed"] == group["total"]) if group else "Pending")
            point_rows.append(values)
    token_rows = []
    for total in totals.values():
        usage = total["usage"]
        token_rows.append([escape(total["label"]), *[number(usage.get(key)) for key in ["inputTokens", "cacheReadTokens", "cacheWriteTokens", "outputTokens", "reasoningTokens", "totalTokens"]],
                           f'{total["elapsed_seconds"] / 60:.1f} min', str(total["tool_calls"])])
    status = f'{len(rows)}/6 pilot attempts recorded. Full suite has not started.'
    body = f'<div class="note"><strong>Pilot decision gate.</strong> {status} Readability is scored separately from correctness. Monetary values are estimates; actual invoice and Codex credit debits are unavailable.</div><div class="grid">' + ''.join(cards) + '</div>'
    body += '<section><h2>First-attempt results</h2><p class="muted">One composite application per language, six independent function points per application. No answer repair or grader feedback. Missing/uncompilable submissions score zero end-to-end checks. Code readability is unavailable when no source was submitted.</p>' + table(["Language", "Model", "Outcome", "Checks passed", "Full points", "Readability", "Tokens", "API equivalent"], comparison) + '</section>'
    body += '<section><h2>Where recovery succeeded</h2><div class="filters"><label for="language-filter">Language</label><select id="language-filter"><option value="all">All languages</option><option>C++20</option><option>Rust 2021</option><option>Go</option></select></div><div id="point-table">' + table(["Language", "Function point", "DeepSeek", "GPT-6.1 Sol"], point_rows) + '</div></section>'
    body += '<section><h2>Tokens and execution</h2>' + table(["Model", "Fresh input", "Cache reads", "Cache writes", "Output", "Reasoning", "Total", "Wall time sum", "Tool calls"], token_rows) + '<p class="foot">A dash means unreported, not zero. Provider cache behavior is uncontrolled. Time includes Ghidra and tool latency; it is not pure model inference speed. Reasoning may already be included in provider output totals.</p></section>'
    if len(rows) == 6:
        projection = []
        for total in totals.values():
            remaining = display(5 * total['cost_low'], 15 * total['cost_high']) if total['cost_low'] is not None else 'unavailable'
            projection.append([escape(total['label']), display(total['cost_low'], total['cost_high']), remaining,
                               display(total['cost_per_success_low'], total['cost_per_success_high'])])
        body += '<section><h2>Before the full suite</h2><p>The planned 36-attempt matrix leaves 30 attempts after this pilot. The projection below assumes the remaining attempts use 1–3 times the pilot average tokens at the same rates and cache mix. It is a planning scenario, not a spending cap or confidence interval.</p>' + table(['Model', 'Six-run pilot share', 'Remaining 30 attempts: model share', 'Pilot cost per complete program'], projection) + '<p class="foot">Each model has three pilot attempts and 15 remaining attempts. New programs, retained symbols, cache changes and pricing periods can alter the result. Full-suite execution remains on hold for pilot review.</p></section>'
    body += '<section><h2>Download and inspect</h2><p><a href="data/comparison.json">Complete comparison JSON</a> · <a href="data/comparison.csv">Comparison CSV</a> · <a href="methodology.html">Frozen protocol and pricing</a> · <a href="coverage.html">Implemented and planned grammar coverage</a></p>'
    body += '<p>' + (badge("Protocol audit passed", True) if audit and audit.get("passed") else badge("Protocol audit pending")) + '</p></section>'
    body += """<script>document.getElementById('language-filter').addEventListener('change',event=>{document.querySelectorAll('#point-table tbody tr').forEach(row=>{row.hidden=event.target.value!=='all'&&row.cells[0].textContent!==event.target.value})});</script>"""
    (destination / "index.html").write_text(page("Optimized & stripped: six-run pilot", "DeepSeek V4.1 Flash 1017 vs GPT-6.1 Sol · C++ / Rust / Go · Multiple function points per program", body))

    coverage = '<section><h2>Implemented in every pilot application</h2><p>These source features create realistic compiler output. Equivalent reconstructed code can use different abstractions; passing tests does not prove original grammar recovery.</p>'
    coverage += table(["Point", "Behavior", "Features and containers", "Checks per language"], [[escape(point["id"]), escape(point["title"]), escape(", ".join(point["features"])), "512"] for point in config["points"]]) + '</section>'
    coverage += '<section><h2>ABI coverage and limits</h2>' + table(["Language", "Emitted boundary", "Interpretation"], [
        ["C++", "System V AMD64 and Microsoft x64; eight scalar arguments, 24-byte returned struct", "Distinct routines and call sites verified in private disassembly. Both conventions run inside a Linux ELF."],
        ["Rust", "Native array argument / aggregate return and extern C scalar arguments / repr(C) return", "Separate optimized crate. Distinct routines verified; calling conventions are compiler/version specific."],
        ["Go", "ABIInternal; slice/string descriptors, interface calls, eight scalar values and a struct result", "Trivial receiver bridge inlines. No C ABI, cgo, or ABI0 claim."],
    ]) + '<p class="foot">This pilot scores behavior across ABI-lowered calls and evidence-based explanations. It does not require a candidate shared library to link against the original binary.</p></section>'
    coverage += '<section><h2>Language-specific source coverage</h2>' + table(["Language", "Grammar and abstraction points", "Concrete containers"], [
        ["C++20", "Templates, generic lambdas, structured bindings, optional values, variant/visit, if constexpr, virtual methods, RAII ownership", "string, array, vector, map, unordered_map, set, priority_queue, span, unique_ptr"],
        ["Rust 2021", "Enums, match guards, Result/Option, iterator adapters, generic IntoIterator, borrowing, trait objects, pattern destructuring, repr(C)", "String, array, Vec, BTreeMap, HashMap, BTreeSet, BinaryHeap, Box, slices"],
        ["Go", "Generic type constraints, closures, type assertions, interfaces, value/pointer receivers, multiple returns, errors, switch and range", "string, strings.Builder, array, slice, map, map-backed set, container/heap"],
    ]) + '<p class="foot">These are features of the human-written fixtures. Optimization may inline calls, merge branches, specialize generics or erase source abstractions.</p></section>'
    coverage += '<section><h2>Planned after pilot review</h2><div class="pill-list">' + ''.join(f'<span>{escape(item)}</span>' for item in config["planned_extensions"]) + '</div><p class="foot">Planned points are not counted as completed coverage. The full matrix is nine programs × two symbol profiles × two models = 36 attempts, including the six pilot attempts.</p></section>'
    (destination / "coverage.html").write_text(page("Grammar, containers & ABI", "Coverage is explicit: implemented pilot points are separated from later extensions.", coverage, "coverage"))

    methodology = '<section class="narrow"><h2>Frozen evaluation protocol</h2><p>Each model sees only its selected stripped ELF, input record layouts, native submission signature and application entry addresses. Original source, gold vectors, prior outputs, filesystem and execution tools are inaccessible. Ghidra access uses the same eight restricted tools in fresh Harness sessions.</p>'
    methodology += '<ul><li>Normal optimization and inlining: C++ -O2; Rust opt-level=2; Go default optimization.</li><li>Stripped copies retain exactly the same .text as their private optimized originals. Both pass 3,088 oracle checks.</li><li>60 tool calls, 30 minutes, high reasoning, maximum 16,384 output tokens per response. Provider retries disabled.</li><li>One first attempt per model/language. Full visible code is captured unchanged; the grader supplies no feedback.</li><li>512 checks for each of six points plus 16 invalid-dispatch checks. Tests include deterministic random and metamorphic cases.</li><li>Readability: formatting, names, decomposition, language idioms and comments, each 0–2. Source-only model-blinded analyst review; subjective and reported independently.</li><li>Only three programs and one sample per model/program: exploratory results cannot establish broad model rankings.</li></ul></section>'
    methodology += '<section><h2>Model identity and comparison limits</h2><p>The exact requested GPT model is gpt-6.1-sol. DeepSeek uses the moving deepseek-flash alias, documented as V4.1 Flash 1017 when this cohort was prepared. Returned model identifiers are preserved when exposed; a moving alias does not provide an immutable server-weight fingerprint. Both use high reasoning, which does not imply equal internal computation.</p><p>Normal stripping retains required dynamic imports and Go runtime metadata. The pilot provides application entry addresses, so entry discovery is outside the score. Functional equivalence does not establish recovery of original source text, ownership or container choice.</p></section>'
    methodology += '<section><h2>Pricing snapshot</h2><p>Published-rate API equivalents exclude this coordinating conversation, environment setup, local compute and unlaunched runs. Every completed inference step, including failed attempts, counts. Actual per-task billed currency is unavailable. GPT through Codex uses the existing subscription; these figures are not a subscription invoice.</p>'
    methodology += table(["Model / period", "Fresh input / 1M", "Cached input / 1M", "Cache write / 1M", "Output / 1M"], [
        ["DeepSeek / off-peak", "$0.15", "$0.003", "$0.15*", "$0.60"], ["DeepSeek / peak", "$0.30", "$0.006", "$0.30*", "$1.20"],
        ["GPT-6.1 Sol / standard ≤272K", "$2.00", "$0.10", "$2.50", "$10.00"],
        ["GPT-6.1 Sol / standard >272K", "$4.00", "$0.20", "$5.00", "$15.00"],
    ]) + '<p class="foot">*DeepSeek cache misses use the fresh-input rate; no separate cache-write fee is assumed. DeepSeek peak periods are weekdays 01:00–04:00 and 06:00–10:00 UTC excluding Chinese public holidays. GPT cache writes may be unreported: low/high estimates price fresh input at the normal or cache-write rate. Cache counts are preserved as reported.</p>'
    methodology += f'<p>Rates verified: {escape(prices["verified_at_utc"])}.</p><p><a href="https://api-docs.deepseek.com/quick_start/pricing/">DeepSeek official pricing</a> · <a href="https://api-docs.deepseek.com/updates/">DeepSeek release identity</a> · <a href="https://developers.openai.com/api/docs/models/gpt-6.1-sol">OpenAI official model and pricing</a></p></section>'
    methodology += '<section><h2>Reproducibility</h2><p>Binary/prompt/test hashes, compiler manifests, full event streams, unchanged submissions, tool evidence, grading receipts and pipeline snapshots are retained in the local cohort directory. The current cohort is ' + escape(run_id) + '.</p><p><a href="data/comparison.json">Machine-readable results</a> · <a href="data/provenance.json">Frozen input provenance</a></p></section>'
    (destination / "methodology.html").write_text(page("Method, fairness & cost", "Traceable first attempts with separate behavioral and readability assessments.", methodology, "methodology"))
    shutil.copy2(cohort / "input-provenance.json", destination / "data/provenance.json")
    if audit:
        (destination / "data/protocol-audit.json").write_text(json.dumps(audit, indent=2) + "\n")

    for language, title in LANGUAGES.items():
        selected = [row for row in rows if row["language"] == language]
        details = '<div class="note">Unchanged model submissions appear below. Authored reference source is included for human comparison after evaluation; models could not access it.</div>'
        for row in selected:
            grade = row["grade"]
            details += f'<section><div class="eyebrow">{escape(row["model"])}</div><h2>{escape(row["receipt"]["model_label"])}</h2><p>{badge(row["outcome"], True) if grade.get("program_success") else badge(row["outcome"])} · {grade.get("passed", 0):,}/{grade.get("total", 0):,} checks · {grade.get("fully_correct_points", 0)}/6 complete points</p>'
            if not row['code_file']:
                details += '<p>No native source submission was available. Compilation and behavioral tests could not run; this is an end-to-end failure, not evidence that a particular inferred formula was wrong.</p>'
            details += table(["Point", "Passed", "Rate"], [[escape(key), f'{value["passed"]}/{value["total"]}', percent(value)] for key, value in grade.get("points", {}).items()])
            review = row["readability"]
            if review:
                details += f'<h3 style="margin-top:24px">Readability: {review["total"]}/10</h3>' + table(["Dimension", "Score / 2"], [[escape(key.replace("_", " ")), str(value)] for key, value in review["scores"].items()])
                details += '<p style="margin-top:15px">' + escape(review["notes"]) + '</p><p class="muted">Source-only model-blinded coordinating-analyst assessment; subjective.</p>'
            else:
                details += '<p class="pending">Readability review pending.</p>' if row['code_file'] else '<p class="muted">Code readability: unavailable because there is no submission.</p>'
            if row["format"]:
                diagnostic = row["format"]
                details += f'<p class="muted">{diagnostic["lines"]} lines · {diagnostic["lines_over_100_columns"]} lines over 100 columns · {escape(diagnostic["formatter"])} exact style match: {diagnostic["matches_formatter"]}. Formatter match is a diagnostic, not the readability grade.</p>'
            if row["code_file"]:
                source = (destination / "code" / row["code_file"]).read_text()
                details += f'<p><a href="../code/{escape(row["code_file"])}" download>Download original source submission</a></p><pre><code>{escape(source)}</code></pre>'
            answer_path = cohort / row["model"] / language / 'answer.md'
            if answer_path.exists():
                details += '<details><summary>Original explanation and ABI evidence</summary><pre>' + escape(answer_path.read_text()) + '</pre></details>'
            details += '<details><summary>Behavior categories</summary>' + table(["Category", "Passed", "Rate"], [[escape(key), f'{value["passed"]}/{value["total"]}', percent(value)] for key, value in grade.get('categories', {}).items()]) + '</details>'
            if grade.get("first_mismatches"):
                details += '<details><summary>First behavioral mismatches</summary><pre>' + escape(json.dumps(grade["first_mismatches"], indent=2)) + '</pre></details>'
            compile_log = cohort / row["model"] / language / "compile.log"
            if compile_log.exists() and compile_log.read_text().strip():
                details += '<details><summary>Compiler diagnostics</summary><pre>' + escape(compile_log.read_text()) + '</pre></details>'
            details += '</section>'
        details += '<section><h2>Human-written reference fixtures</h2><p>The original application uses descriptive names and standard formatting. This source is private during the model task.</p>'
        for source in sorted((frozen / "authoring" / language).iterdir()):
            if not source.is_file():
                continue
            target_name = f"reference-{language}-{source.name}"
            shutil.copy2(source, destination / "code" / target_name)
            details += f'<details><summary>{escape(source.name)}</summary><p><a href="../code/{escape(target_name)}" download>Download reference</a></p><pre><code>{escape(source.read_text())}</code></pre></details>'
        details += '</section>'
        (destination / f"cases/{language}.html").write_text(page(f"{title}: reconstruction details", "Function-level correctness, source readability and unchanged native code.", details, language, True))
    print(json.dumps({"report": str(destination / "index.html"), "attempts": len(rows), "totals": totals}, indent=2))
    (destination / 'data/report-generator.json').write_text(json.dumps({
        'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'generated_at': report['generated_at'], 'grading_inputs_modified': False,
    }, indent=2) + '\n')


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "complex-pilot-common-budget-20261019")
