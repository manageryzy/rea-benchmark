"""Check run provenance and isolation independently of model correctness."""
import argparse, datetime, hashlib, json, pathlib, re
BENCH = pathlib.Path(__file__).resolve().parents[1]

def read(path):
    return json.loads(path.read_text())

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('run_id')
    args = parser.parse_args()
    base = BENCH / 'runs' / args.run_id
    config = read(base / 'run-manifest.json')['configuration']
    provenance = read(base / 'input-provenance.json')
    allowed = {'mcp__rea__' + t['name'] for t in read(BENCH / 'config/rea-tools.json')['tools']}
    sessions, rows = [], []
    assert len(list(base.glob('*/*/result.json'))) == len(config['run_order'])
    for language, model in config['run_order']:
        run = base / model / language
        result, grade = read(run / 'result.json'), read(run / 'grade.json')
        audit = [json.loads(s) for s in (run / 'tool-audit.jsonl').read_text().splitlines()]
        trace = [json.loads(s) for s in (run / 'tool-calls.jsonl').read_text().splitlines()]
        events = [json.loads(s) for s in (run / 'events.jsonl').read_text().splitlines()]
        public = BENCH / 'suites/record-parser/public' / language
        assert result['binary_sha256'] == sha(public / 'target') == provenance['public_inputs'][language]['target']
        assert result['prompt_sha256'] == sha(run / 'prompt.txt') == provenance['public_inputs'][language]['prompt.txt']
        assert grade['vectors_sha256'] == provenance['vectors_sha256'] == sha(BENCH / 'suites/record-parser/grading/vectors.json')
        assert result['output_language'] == grade['output_language'] == language
        assert grade['total'] == len(read(BENCH / 'suites/record-parser/grading/vectors.json'))
        assert len(audit) == 1 and set(audit[0]['tools']) == allowed
        assert not audit[0]['source_access'] and not audit[0]['execution_access']
        assert len(trace) == result['tool_calls'] <= config['max_tool_calls']
        assert all('mcp__rea__' + t['tool'] in allowed for t in trace)
        for call in trace:
            if call['tool'] == 'open_binary' and not call['is_error']:
                assert call['arguments'] == {'path': str(public / 'target'), 'provider_id': 'ghidra'}
            if not call['is_error']:
                assert 'document' not in call['arguments']
        session = [e['sessionId'] for e in events if e['type'] == 'session']
        assert len(session) == 1
        sessions += session
        # Rebuild visible text without touching the saved answer or grading.
        messages, chunks = [], []
        for event in events:
            if event['type'] == 'status' and event.get('phase') in ('step_start', 'step_end'):
                if chunks: messages.append(''.join(chunks)); chunks = []
            elif event['type'] == 'text':
                chunks.append(event.get('text', ''))
        if chunks: messages.append(''.join(chunks))
        visible = '\n\n'.join(messages)
        assert visible == (run / 'answer.md').read_text()
        suffix = {'cpp': 'cpp', 'rust': 'rs', 'go': 'go'}[language]
        candidate = run / ('reconstructed.' + suffix)
        if grade['compiled']:
            assert sha(candidate) == grade['candidate_source_sha256']
            assert candidate.read_text() in visible
        rows.append({'model': model, 'language': language,
                     'native_compiled': grade['compiled'], 'passed': grade['passed'], 'total': grade['total'],
                     'same_inputs': True, 'visible_answer_preserved': True, 'allowed_tools': len(allowed),
                     'tool_calls': len(trace), 'tool_errors': result['tool_errors'],
                     'explicit_close': bool(trace and trace[-1]['tool'] == 'close_binary' and not trace[-1]['is_error'])})
    assert len(set(sessions)) == len(sessions)
    credentials = list((BENCH.parent / '.cache/benchmark-runtime').glob('home-*/.credentials.yaml'))
    assert not credentials, 'Benchmark supervisor must finish and remove temporary credentials before audit'
    for name, digest in provenance['files_sha256'].items():
        assert sha(base / 'pipeline' / name) == digest
    amendment = read(base / 'pipeline-amendments/answer-capture.json')
    for name, digest in amendment['files_sha256'].items():
        assert sha(base / 'pipeline-amendments/answer-capture' / pathlib.Path(name).name) == digest
    out = base / 'analysis/protocol-audit.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    report = {'passed': True, 'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'attempts': len(rows), 'fresh_sessions': len(sessions), 'model_retries': 0,
              'candidate_edits': 0, 'temporary_benchmark_credentials_removed': True,
              'vectors_sha256': provenance['vectors_sha256'], 'runs': rows,
              'answer_capture_amendment': 'pipeline-amendments/answer-capture.json'}
    out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))

if __name__ == '__main__':
    main()
