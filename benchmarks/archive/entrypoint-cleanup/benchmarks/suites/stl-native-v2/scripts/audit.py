"""Audit archived first attempts without editing or re-running inference."""
import hashlib
import json
from pathlib import Path
import sys

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]
ROOT = BENCH.parent


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(run_id='stl-rust-data-v2-20261019'):
    cohort = BENCH / 'runs' / run_id
    manifest = json.loads((cohort / 'run-manifest.json').read_text())
    provenance = json.loads((cohort / 'input-provenance.json').read_text())
    errors, checks, sessions = [], [], []
    def check(condition, description):
        checks.append({'check': description, 'passed': bool(condition)})
        if not condition: errors.append(description)
    for index, (path, digest) in enumerate(provenance['protocol_files_sha256'].items()):
        archived = cohort / 'pipeline' / f'{index:02d}-{Path(path).name}'
        check(sha(archived) == digest, f'frozen protocol file {index}')
        check(sha(Path(path)) == digest, f'live grading/protocol input remains unchanged: {Path(path).name} ({index})')
    catalog_path = next(path for path in provenance['protocol_files_sha256'] if path.endswith('rea-tools-data-v2.json'))
    allowed = {tool['name'] for tool in json.loads(Path(catalog_path).read_text())['tools']}
    check(len(allowed) == 12, 'twelve tools shared by both models')
    for case, model in manifest['configuration']['run_order']:
        directory = cohort / model / case
        completed_path = directory / 'completed.json'
        if not completed_path.exists():
            check(False, f'completed receipt missing: {model}/{case}')
            continue
        completed = json.loads(completed_path.read_text())
        for name, digest in completed['files_sha256'].items(): check(sha(directory / name) == digest, f'preserved {model}/{case}/{name}')
        receipt = json.loads((directory / 'result.json').read_text())
        grade = json.loads((directory / 'grade.json').read_text())
        inputs = provenance['public_inputs'][case]
        for name, digest in inputs.items(): check(sha(cohort / 'inputs/public' / case / name) == digest, f'frozen {case}/{name}')
        check(receipt['binary_sha256'] == inputs['target'], f'selected target digest {model}/{case}')
        check(receipt['prompt_sha256'] == inputs['prompt.txt'], f'selected prompt digest {model}/{case}')
        check(receipt['protocol_sha256'] == manifest['protocol_sha256'], f'protocol digest {model}/{case}')
        check(not grade['candidate_modified'], f'candidate unedited {model}/{case}')
        extension = 'rs' if receipt['language'] == 'rust' else 'cpp'
        source = directory / f'reconstructed.{extension}'
        if source.exists(): check(sha(source) == grade['candidate_source_sha256'], f'source matches grade {model}/{case}')
        trace_path = directory / 'tool-calls.jsonl'
        trace = [json.loads(line) for line in trace_path.read_text().splitlines()] if trace_path.exists() else []
        check(all(call['tool'] in allowed for call in trace), f'only allowlisted tools {model}/{case}')
        for call in trace:
            check('document' not in call['arguments'], f'no document override {model}/{case}/{call["sequence"]}')
            if call['tool'] == 'open_binary':
                check(Path(call['arguments']['path']) == cohort / 'inputs/public' / case / 'target', f'only selected ELF opened {model}/{case}/{call["sequence"]}')
            if call['tool'] == 'read_bytes': check(call['arguments']['length'] <= 4096, f'bounded bytes {model}/{case}/{call["sequence"]}')
            if call['tool'] == 'search_bytes': check(call['arguments']['limit'] <= 200, f'bounded search {model}/{case}/{call["sequence"]}')
            evidence = directory / f"evidence-{call['sequence']:02d}-{call['tool']}.json"
            check(evidence.exists(), f'tool Evidence retained {model}/{case}/{call["sequence"]}')
        # Closing failures are reported as compliance outcomes rather than erased.
        messages_path = directory / 'assistant-messages.jsonl'
        if messages_path.exists():
            messages = [json.loads(line) for line in messages_path.read_text().splitlines()]
            if messages:
                ids = {message['session_id'] for message in messages}
                check(len(ids) == 1, f'single source session {model}/{case}')
                sessions.extend(ids)
                check((directory / 'answer.md').read_text() == '\n\n'.join(message['text'] for message in messages), f'lossless committed visible answer {model}/{case}')
    check(len(sessions) == len(set(sessions)), 'fresh session identity for every captured attempt')
    home = Path(manifest['runtime_home'])
    check(not (home / '.credentials.yaml').exists(), 'temporary credential file removed after cohort')
    amendment_path = cohort / 'grading-amendment-framed.json'
    if amendment_path.exists():
        amendment = json.loads(amendment_path.read_text())
        check(amendment['status'] == 'complete' and len(amendment['grades']) == 10, 'uniform framed grading for all ten attempts')
        check(amendment['candidate_edits'] == 0 and amendment['new_model_calls'] == 0, 'framing amendment changes no candidates or inference')
        check(amendment['validation']['passed'] and len(amendment['validation']['controls']) == 12, 'twelve frame/empty-input/newline/noise/crash controls pass')
        check(sha(SUITE / 'scripts/grade-framed.py') == amendment['grader_sha256'], 'framed grader matches amendment')
        archive = cohort / 'grading-pipeline/grade-framed-final.py'
        check(archive.exists() and sha(archive) == amendment['grader_sha256'], 'framed grader source archived')
        for item in amendment['grades']:
            directory = cohort / item['model'] / item['case']
            check(sha(directory / 'grade.json') == item['old_grade_sha256'], f'original batch grade preserved {item["model"]}/{item["case"]}')
            check(sha(directory / 'grade-framed.json') == item['new_grade_sha256'], f'framed grade preserved {item["model"]}/{item["case"]}')
            framed = json.loads((directory / 'grade-framed.json').read_text())
            for name, digest in framed['artifacts_sha256'].items(): check(sha(directory / 'grade-framed' / name) == digest, f'framed artifact preserved {item["model"]}/{item["case"]}/{name}')
            extension = 'rs' if item['case'].startswith('rust') else 'cpp'
            source = directory / f'reconstructed.{extension}'
            check((not source.exists()) if item['source_sha256'] is None else source.exists() and sha(source) == item['source_sha256'], f'unchanged candidate through frame amendment {item["model"]}/{item["case"]}')
    reviews_path = cohort / 'readability-reviews.json'
    if reviews_path.exists():
        reviews = json.loads(reviews_path.read_text())
        commit = json.loads((cohort / 'blind-review/ratings-commit.json').read_text())
        check(sha(cohort / 'blind-review/ratings.json') == commit['ratings_sha256'], 'source readability ratings match pre-unblinding commitment')
        check(reviews['rating_commit'] == commit and len(reviews['reviews']) == 10, 'all ten committed source reviews attached')
        for key, review in reviews['reviews'].items():
            model, case = key.split('/')
            source = cohort / model / case / ('reconstructed.rs' if case.startswith('rust') else 'reconstructed.cpp')
            check(sha(source) == review['candidate_source_sha256'], f'reviewed source unchanged {key}')
            check(sum(review['scores'].values()) == review['total'] and all(type(value) is int and 0 <= value <= 2 for value in review['scores'].values()), f'readability rubric arithmetic {key}')
    for name in ['duplicate-guard-validation.json', 'cache-replay-validation.json']:
        path = cohort / name
        if path.exists(): check(json.loads(path.read_text())['passed'], f'no-duplicate inference validation: {name}')
    results = []
    for case, model in manifest['configuration']['run_order']:
        path = cohort / model / case / 'result.json'
        if path.exists():
            r = json.loads(path.read_text())
            results.append({'case': case, 'model': model, 'tool_calls': r['tool_calls'], 'tool_errors': r['tool_errors'],
                'data_tool_calls': r['data_tool_calls'], 'explicit_close': r['explicit_close'], 'exit_code': r['exit_code'], 'timed_out': r['timed_out']})
    audit = {'passed': not errors, 'errors': errors, 'checks': checks, 'attempts': results, 'session_count': len(set(sessions)),
        'candidate_edits': 0, 'automatic_inference_retries': 0, 'shared_host_concurrency': 2,
        'scope': 'Compliance and integrity audit, not a correctness guarantee or source-identity proof.'}
    (cohort / 'protocol-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps({'passed': audit['passed'], 'errors': errors, 'checks': len(checks), 'sessions': audit['session_count']}))
    return audit


if __name__ == '__main__':
    audit = main(sys.argv[1] if len(sys.argv) > 1 else 'stl-rust-data-v2-20261019')
    sys.exit(0 if audit['passed'] else 1)
