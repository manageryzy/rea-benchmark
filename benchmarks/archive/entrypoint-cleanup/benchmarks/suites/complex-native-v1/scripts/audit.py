"""Verify source-blind first attempts, immutable inputs and candidate provenance."""

import datetime
import hashlib
import json
from pathlib import Path
import re
import sys

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]
ROOT = BENCH.parent


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(run_id):
    base = BENCH / "runs" / run_id
    manifest = read(base / "run-manifest.json")
    config = manifest["configuration"]
    provenance = read(base / "input-provenance.json")
    archived_inputs = base / 'inputs'
    input_root = archived_inputs if archived_inputs.exists() else SUITE
    allowed = {"mcp__rea__" + tool["name"] for tool in read(base / "pipeline/shared/rea-tools.json")["tools"]}
    assert len(allowed) == 8
    assert len(list(base.glob("*/*/result.json"))) == len(config["run_order"]) == 6
    for name, digest in provenance["files_sha256"].items():
        assert sha(base / "pipeline" / name) == digest, name
    for name, digest in provenance["private_inputs"].items():
        assert sha(input_root / name) == digest, name
    for language, entries in provenance["public_inputs"].items():
        for name, digest in entries.items():
            path = input_root / 'public' / language / name if archived_inputs.exists() else SUITE / 'public' / language / config['profile'] / name
            assert sha(path) == digest
    vectors_hash = provenance["private_inputs"]["private/vectors.json"]
    vector_count = len(read(input_root / "private/vectors.json"))
    assert vector_count == 3088
    sessions, rows = [], []
    for language, model in config["run_order"]:
        directory = base / model / language
        receipt, grade = read(directory / "result.json"), read(directory / "grade.json")
        audit = [json.loads(line) for line in (directory / "tool-audit.jsonl").read_text().splitlines()]
        trace = [json.loads(line) for line in (directory / "tool-calls.jsonl").read_text().splitlines()]
        events = [json.loads(line) for line in (directory / "events.jsonl").read_text().splitlines()]
        public = SUITE / "public" / language / config["profile"]
        assert receipt["binary_sha256"] == provenance["public_inputs"][language]["target"]
        assert receipt["prompt_sha256"] == sha(directory / "prompt.txt") == provenance["public_inputs"][language]["prompt.txt"]
        assert grade["vectors_sha256"] == vectors_hash
        assert grade["total"] == vector_count
        assert grade["output_language"] == receipt["output_language"] == language
        assert len(audit) == 1 and set(audit[0]["tools"]) == allowed
        assert not audit[0]["source_access"] and not audit[0]["execution_access"]
        assert len(trace) == receipt["tool_calls"] <= config["max_tool_calls"]
        assert all("mcp__rea__" + call["tool"] in allowed for call in trace)
        for call in trace:
            if call["tool"] == "open_binary" and not call["is_error"]:
                assert call["arguments"] == {"path": str(public / "target"), "provider_id": "ghidra"}
            if not call["is_error"]:
                assert "document" not in call["arguments"]
        session = [event["sessionId"] for event in events if event["type"] == "session"]
        assert len(session) == 1
        sessions.extend(session)
        messages, chunks, projected = [], [], []
        final, step = "", None
        for event in events:
            if event["type"] == "status" and event.get("phase") in ("step_start", "step_end"):
                if event.get('phase') == 'step_start':
                    step = event.get('step')
                if chunks:
                    messages.append("".join(chunks))
                    chunks = []
            elif event["type"] == "text":
                chunks.append(event.get("text", ""))
                projected.append({'step': step, 'text': event.get('text', ''), 'truncated': event.get('truncated', False)})
            elif event["type"] == "final":
                final = event.get("text", "")
        if chunks:
            messages.append("".join(chunks))
        committed = [json.loads(line) for line in (directory / 'assistant-messages.jsonl').read_text().splitlines()]
        assert all(message['session_id'] == session[0] for message in committed)
        blocks = [{'step': message['step'], 'text': text} for message in committed for text in message['blocks']]
        assert len(blocks) == len(projected)
        for complete, projection in zip(blocks, projected):
            assert complete['step'] == projection['step']
            if projection['truncated']:
                assert complete['text'].startswith(projection['text'])
            else:
                assert complete['text'] == projection['text']
        visible = '\n\n'.join(message['text'] for message in committed)
        if final:
            assert committed and committed[-1]['text'] == final
        assert visible == (directory / "answer.md").read_text()
        extension = {"cpp": "cpp", "rust": "rs", "go": "go"}[language]
        candidate = directory / f"reconstructed.{extension}"
        if candidate.exists():
            assert sha(candidate) == grade["candidate_source_sha256"]
            assert candidate.read_text() in visible
        assert not grade["candidate_modified"]
        rows.append({"model": model, "language": language, "compiled": grade["compiled"],
                     "passed": grade["passed"], "total": grade["total"], "tool_calls": len(trace),
                     "tool_errors": receipt["tool_errors"], "same_inputs": True,
                     "candidate_preserved": True, "allowed_tools": len(allowed),
                     "explicit_close": bool(trace and trace[-1]["tool"] == "close_binary" and not trace[-1]["is_error"])})
    assert len(set(sessions)) == 6
    assert not (Path(manifest["runtime_home"]) / ".credentials.yaml").exists(), "Supervisor must exit before audit"
    amendment_dir = base / 'pipeline-amendments/rust-library-packaging'
    amendment = read(amendment_dir / 'amendment.json')
    assert amendment['candidate_edits'] == amendment['model_calls_added'] == 0
    for name, digest in amendment['files_sha256'].items():
        assert sha(amendment_dir / name) == digest
    assert read(amendment_dir / 'controls/validation.json')['passed']
    assert sha(amendment_dir / 'controls/validation.json') == amendment['control_validation_sha256']
    for model in ['deepseek-flash', 'gpt-6.1-sol']:
        old = read(amendment_dir / (model + '-grade.json'))
        new = read(base / model / 'rust/grade.json')
        assert old['candidate_source_sha256'] == new['candidate_source_sha256']
        assert new['rust_packaging'] == 'separate library crate'
    report = {"passed": True, "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "attempts": len(rows), "fresh_sessions": len(sessions), "provider_retries_disabled": True,
              "candidate_edits": 0, "full_suite_started": False, "temporary_cohort_credentials_removed": True,
              "vectors_sha256": vectors_hash, "points_per_program": 6, "checks_per_program": vector_count,
              "pipeline_snapshot_verified": True, "public_and_private_input_hashes_verified": True, "runs": rows}
    report['grading_amendments'] = ['Rust library packaging supports valid inner documentation; both unchanged Rust candidates regraded uniformly.']
    report['same_documented_output_ceiling'] = config['max_output_tokens']
    report['identical_wire_cap_enforcement'] = False
    report['wire_cap_difference_disclosed'] = True
    if (base / 'input-archive.json').exists():
        archive = read(base / 'input-archive.json')
        for name, digest in archive['files_sha256'].items():
            assert sha(base / 'inputs' / name) == digest
        report['exact_inputs_archived'] = True
    reviews = read(base / 'readability-reviews.json')
    assert reviews['ratings_sha256'] == sha(base / 'blind-review/ratings.json')
    assert len(reviews['reviews']) == 6
    for key, review in reviews['reviews'].items():
        model, language = key.split('/')
        grade = read(base / model / language / 'grade.json')
        assert review['candidate_source_sha256'] == grade['candidate_source_sha256']
        assert sum(review['scores'].values()) == review['total']
    report['source_only_readability_reviews_verified'] = True
    destination = base / "analysis"
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "protocol-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "complex-pilot-common-budget-20261019")
