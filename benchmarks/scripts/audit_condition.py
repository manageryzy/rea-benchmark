#!/usr/bin/python3
"""Check saved fixed-condition evidence delivery without rerunning inference."""
import hashlib
import json
from pathlib import Path

import audit_environment
import records

ROOT, BENCH = records.ROOT, records.BENCH
OUTPUT = BENCH / 'config/condition-evidence-audit.json'


def audit_paging(row, entry):
    """Bind delivered projections and retrieved slices to their complete originals."""
    directory = ROOT / row['directory']
    references, checked, retrieved, constraints = {}, 0, 0, []

    def read(path):
        raw = path.read_bytes()
        entry['inputs_sha256'][str(path.relative_to(ROOT))] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    def primary(result):
        return '\n'.join(b['text'] for b in result.get('content', []) if b.get('type') == 'text')

    def digest(text):
        return hashlib.sha256(text.encode()).hexdigest()

    def selected(text, pointer):
        if not pointer:
            return text
        value = json.loads(text)
        for token in pointer[1:].split('/'):
            key = token.replace('~1', '/').replace('~0', '~')
            value = value[int(key)] if isinstance(value, list) else value[key]
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(',', ':'))

    def verify_page(page, original):
        text = selected(original, page['pointer'])
        start, end = page['offset'], page['end_offset']
        assert 0 <= start <= end <= len(text)
        assert page['text'] == text[start:end] and page['total_characters'] == len(text)
        assert page['view_sha256'] == digest(text)
        assert page['complete'] == (end == len(text))
        assert page['next_offset'] == (None if end == len(text) else end)

    trace = records.read_trace(directory / 'tool-calls.jsonl')
    for call in trace:
        sequence = call['sequence']
        originals = list(directory.glob(f'evidence-{sequence:02d}-*.json'))
        projected = list(directory.glob(f'model-evidence-{sequence:02d}-*.json'))
        assert len(originals) == len(projected) == 1
        raw, model = read(originals[0]), read(projected[0])
        original, delivered = primary(raw), primary(model)
        assert len(delivered) <= 24000 and bool(raw.get('isError')) == bool(model.get('isError'))
        if call['tool'] == 'read_evidence':
            if raw.get('isError'):
                assert original == delivered
                continue
            page = json.loads(delivered)
            args = call['arguments']
            assert page['evidence_ref'] == args['evidence_ref']
            assert page['pointer'] == args.get('pointer', '') and page['offset'] == args.get('offset', 0)
            assert page['end_offset'] - page['offset'] <= args.get('limit', 12000)
            verify_page(page, references[page['evidence_ref']])
            retrieved += 1
            continue
        # REA's structured copy is redundant in these observed live results.
        # Fail closed if a new, unique structured field would need JS canonicalization.
        if 'structuredContent' in raw:
            assert json.loads(original) == raw['structuredContent'], 'Unique structured fields need explicit canonical verification'
        if raw.get('isError'):
            try:
                error = json.loads(original).get('error', {})
            except ValueError:
                error = {}
            if error.get('category') == 'resource_constraint':
                constraints.append({'sequence': sequence, 'tool': call['tool'], 'code': error.get('code'),
                                    'message': error.get('message'), 'details': error.get('details')})
        if len(original) <= 24000:
            assert delivered == original
            continue
        projection = json.loads(delivered)
        assert projection['delivery'] == 'explicit_lossless_paging'
        assert projection['original_sha256'] == digest(original)
        assert projection['original_characters'] == len(original)
        assert projection['original_utf8_bytes'] == len(original.encode())
        ref = 'sha256:' + digest(original)
        assert projection['evidence_ref'] == ref
        references[ref] = original
        verify_page(projection['primary_view'], original)
        for view in projection['available_views']:
            text = selected(original, view['pointer'])
            assert view['characters'] == len(text) and view['view_sha256'] == digest(text)
        checked += 1
    if row['harness'] == 'deepseek-harness':
        available = 'mcp__rea__read_evidence' in entry.get('tool_names', [])
    else:
        requests = sorted((directory / 'wire').glob('request-*.json'))
        request = read(requests[-1])
        available = any(t.get('function', {}).get('name', '').endswith('read_evidence') for t in request.get('tools', []))
    assert available, 'The common paging tool was unavailable to this client'
    entry['paging'] = {'original_hashes_and_slices_verified': True, 'retrieval_tool_available': available,
                       'large_originals': checked, 'retrieval_pages': retrieved,
                       'backend_resource_constraints': constraints,
                       'scope': 'All returned originals retained; exact model projections and requested slices. Unrequested pages are not claimed as read.'}


def main():
    audit_environment.require_zstd()
    data = records.collect()
    sessions = {}
    for path in (ROOT / '.cache/benchmark-runtime').glob('home-*/sessions/*/*/session.v4.jsonl.zstd'):
        sessions.setdefault(path.parent.name, []).append(path)
    cases = []
    for row in data['condition_records']:
        directory = ROOT / row['directory']
        if not (directory / 'result.json').exists() or not row['grade']:
            continue
        if row['harness'] == 'deepseek-harness':
            prefix = 'model-evidence-' if row['condition'] == 'paged-evidence-v2' else 'evidence-'
            entry = audit_environment.audit_case(row, sessions, prefix, verify_errors=True)
            entry['condition'] = row['condition']
            if row['condition'] == 'paged-evidence-v2':
                audit_paging(row, entry)
            cases.append(entry)
            continue
        requests = sorted((directory / 'wire').glob('request-*.json'))
        if not requests:
            continue
        inputs = {}
        def read(path):
            raw = path.read_bytes()
            inputs[str(path.relative_to(ROOT))] = hashlib.sha256(raw).hexdigest()
            return raw
        for name in ['result.json', 'tool-calls.jsonl']:
            if (directory / name).exists():
                read(directory / name)
        last = json.loads(read(requests[-1]))
        tools = [m for m in last['messages'] if m['role'] == 'tool']
        prefix = 'model-evidence-' if row['condition'] == 'paged-evidence-v2' else 'evidence-'
        evidence = sorted(directory.glob(prefix+'[0-9]*.json'))
        full, errors, missing, clips, duplicates = 0, 0, 0, 0, 0
        details = []
        for path in evidence:
            value = json.loads(read(path))
            primary = '\n'.join(b['text'] for b in value.get('content', []) if b.get('type') == 'text')
            exact = next((m for m in tools if primary and primary in m.get('content', '')), None)
            if value.get('isError'):
                errors += 1
                status = 'tool_error' if exact else 'error_delivery_unverified'
                missing += not bool(exact)
            elif exact:
                full += 1
                status = 'primary_complete'
            else:
                missing += 1
                status = 'not_observed_in_last_request'
            if exact:
                tools.remove(exact)
                clips += '[Tool output truncated by resultBudget:' in exact['content']
                duplicates += 'Structured content:\n' in exact['content']
            details.append({'evidence_path': str(path.relative_to(ROOT)), 'primary_sha256': hashlib.sha256(primary.encode()).hexdigest(),
                            'primary_bytes': len(primary.encode()), 'status': status})
        entry = {'condition': row['condition'], 'experiment': row['experiment'], 'case': row['case']['id'], 'model': row['model'],
            'status': 'primary_complete' if not missing and not clips else 'unverified',
            'verification_layer': 'saved outgoing HTTP tool messages; exact primary text containment',
            'inputs_sha256': inputs, 'counts': {'primary_complete': full, 'tool_error': errors, 'unverified': missing},
            'clips': clips, 'structured_duplicate_tails': duplicates, 'tools': details}
        if row['condition'] == 'paged-evidence-v2':
            audit_paging(row, entry)
        cases.append(entry)
    value = {'schema_version': 2, 'scope': 'All completed fixed-condition attempts', 'model_api_calls': 0,
             'auditor_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
             'auditor_implementation_sha256': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in [Path(__file__), Path(audit_environment.__file__), Path(records.__file__)]},
             'certified_model_only_comparison': False, 'cases': cases}
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    temporary.replace(OUTPUT)
    print(f'OFFLINE fixed-condition audit: {len(cases)} completed cases; '
          f'{sum(c.get("counts", {}).get("spilled", 0) for c in cases)} DSH spills; '
          f'{sum(c.get("clips", 0) for c in cases)} observed ZCode clips; zero model calls')


if __name__ == '__main__':
    main()
