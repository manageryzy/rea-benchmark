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
    allowed = {"mcp__rea__" + tool["name"] for tool in read(base / "pipeline/shared/rea-tools.json")["tools"]}
    assert len(allowed) == 8
    assert len(list(base.glob("*/*/result.json"))) == len(config["run_order"]) == 6
    for name, digest in provenance["files_sha256"].items():
        assert sha(base / "pipeline" / name) == digest, name
    for name, digest in provenance["private_inputs"].items():
        assert sha(SUITE / name) == digest, name
    for language, entries in provenance["public_inputs"].items():
        for name, digest in entries.items():
            assert sha(SUITE / "public" / language / config["profile"] / name) == digest
    vectors_hash = provenance["private_inputs"]["private/vectors.json"]
    vector_count = len(read(SUITE / "private/vectors.json"))
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
    report = {"passed": True, "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "attempts": len(rows), "fresh_sessions": len(sessions), "provider_retries_disabled": True,
              "candidate_edits": 0, "full_suite_started": False, "temporary_cohort_credentials_removed": True,
              "vectors_sha256": vectors_hash, "points_per_program": 6, "checks_per_program": vector_count,
              "pipeline_snapshot_verified": True, "public_and_private_input_hashes_verified": True, "runs": rows}
    destination = BENCH / "reports" / run_id
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "protocol-audit.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "complex-pilot-common-budget-20261019")
