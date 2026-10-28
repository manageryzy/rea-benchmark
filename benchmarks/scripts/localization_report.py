"""Append-only, data-only localization regrading and its sanitized report overlay.

Inference artifacts and original grade selections are never changed. No model
client, coordinator, target builder or acceptance suite is invoked here.
"""
import copy
import datetime
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STORE = ROOT / '.cache/benchmark-runtime/localization-contract-20261027'
POLICY = 'localization-exact-json-v1'
IDENTITY = ('model', 'task_id', 'family_id', 'profile_id', 'build_id', 'binary_sha256',
            'public_package_sha256', 'source_language', 'output_language', 'hint_profile', 'capability_ids')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def normalize(raw, kind):
    """Exact representation changes only; never use truth to repair an answer."""
    from grading import artifacts
    from capabilities.optimized_locations import artifact as optimized
    value = artifacts.decode(raw)
    changes = []

    def number(item, path, root=False):
        original = copy.deepcopy(item)
        if root and isinstance(item, dict):
            # Only an explicitly named RVA is an address. A range, VA, symbol,
            # or competing address field is ambiguous and is not guessed.
            metadata = {'name', 'role', 'note', 'signature', 'end_rva'}
            if 'rva' not in item or set(item) - metadata - {'rva'}:
                raise ValueError('ambiguous_root_object')
            if any(not isinstance(v, str) for k, v in item.items() if k in metadata - {'end_rva'}):
                raise ValueError('ambiguous_root_metadata')
            item = item['rva']
        if isinstance(item, str):
            if re.fullmatch(r'0[xX][0-9a-fA-F]+', item):
                item = int(item, 16)
            elif re.fullmatch(r'(?:0|[1-9][0-9]*)', item):
                item = int(item, 10)
            else:
                raise ValueError('non_exact_integer_text')
        if type(item) is not int or not 0 <= item < 2**64:
            raise ValueError('invalid_integer_type_or_range')
        if type(original) is not int:
            changes.append({'path': path, 'original': original, 'integer': item})
        return item

    try:
        value['schema_version'] = number(value['schema_version'], '/schema_version')
        if kind == 'localization':
            value['roots'] = [number(v, f'/roots/{i}', root=True) for i, v in enumerate(value['roots'])]
            for field, keys in (('sites', ('start', 'end')), ('dispatch', ('site', 'target')),
                                ('references', ('site', 'object', 'length'))):
                for i, item in enumerate(value[field]):
                    for key in keys:
                        item[key] = number(item[key], f'/{field}/{i}/{key}')
            chain = value['chain']
            if chain is not None:
                chain['root'] = number(chain['root'], '/chain/root')
                chain['nodes'] = [number(v, f'/chain/nodes/{i}') for i, v in enumerate(chain['nodes'])]
                chain['edges'] = [[number(v, f'/chain/edges/{i}/{j}') for j, v in enumerate(edge)]
                                  for i, edge in enumerate(chain['edges'])]
            artifacts.locations(value)
        elif kind == 'optimized_localization':
            for i, instance in enumerate(value['instances']):
                for j, part in enumerate(instance['ranges']):
                    for key in ('start', 'end'):
                        part[key] = number(part[key], f'/instances/{i}/ranges/{j}/{key}')
            optimized.admit(canonical(value))
        else:
            raise ValueError('unsupported_localization_kind')
    except (KeyError, TypeError, IndexError) as error:
        raise ValueError('invalid_artifact_shape') from error
    return canonical(value), changes


def effective(row, entries):
    """Do not turn a format rejection into a semantic zero, even without an overlay."""
    if not row or not row.get('capability_ids') or any(not c.startswith('L') for c in row['capability_ids']):
        return row
    revision = entries.get(row.get('attempt_id'))
    if revision and all(row.get(k) == revision['observation'].get(k) for k in IDENTITY):
        old = row.get('grade')
        # Never apply an overlay to another selected grade of the same attempt.
        if old and old['grading_id'] == revision['original_grading_id']:
            return {**row, 'grade': revision['grade'], 'localization_revision': revision,
                    'status': row['status'] if revision['grade'] else 'localization_format_review'}
    if row.get('grade') and not row['grade']['artifact_valid']:
        return {**row, 'grade': None, 'status': 'localization_format_review'}
    return row


def history(data):
    revisions = {e['observation']['attempt_id']: e for e in data.get('localization_regrades', {}).get('entries', [])}
    rows = {r['attempt_id']: r for r in data.get('capability_measurements', {}).get('history', [])}
    # A completed, source-verified offline observation may be newer than the
    # base report snapshot. It carries its own explicit regrade timestamp.
    for attempt, entry in revisions.items():
        rows.setdefault(attempt, entry['observation'])
    return [effective(row, revisions) for row in rows.values()]


def load(read_bytes=Path.read_bytes):
    """Verify every local regrade input/result before exposing its safe summary."""
    index = STORE / 'index.json'
    if not index.exists():
        return None
    saved = json.loads(read_bytes(index))
    entries = []
    for item in saved['revisions']:
        path = STORE / 'regrades' / item['revision_id'] / 'revision.json'
        raw = read_bytes(path)
        if sha(raw) != item['sha256']:
            raise ValueError('Localization regrade receipt changed')
        receipt = json.loads(raw)
        if sha(canonical(receipt['identity'])) != item['revision_id']:
            raise ValueError('Localization regrade identity changed')
        for name, digest in receipt['identity']['inputs_sha256'].items():
            p = ROOT / name
            if p.is_symlink() or not p.resolve().is_relative_to(ROOT):
                raise ValueError('Localization regrade input escapes workspace')
            if sha(read_bytes(p)) != digest:
                raise ValueError('Localization regrade source evidence changed: ' + name)
        for name, digest in receipt['outputs_sha256'].items():
            if Path(name).name != name or sha(read_bytes(path.parent / name)) != digest:
                raise ValueError('Localization regrade output changed')
        public = receipt['public']
        if public['revision_id'] != item['revision_id']:
            raise ValueError('Localization regrade public identity changed')
        entries.append(public)
    return {'policy': POLICY, 'updated_at': saved['updated_at'], 'entries': entries}


def regrade():
    """Reuse the real closed data-only graders under the selected local policy."""
    import action_cache
    import startup_policy
    from capabilities import config, measurements, metrics, store, submission
    from grading.artifact_capture import extract
    STORE.mkdir(parents=True, exist_ok=True)
    settings, _ = config.load()
    models_value = json.loads((ROOT / 'benchmarks/config/models.json').read_bytes())
    models = models_value.get('models', models_value)
    if isinstance(models, list):
        models = {r['id']: r for r in models}
    candidates = []
    base = ROOT / 'benchmarks/runs/capabilities-v1'
    for model in base.iterdir():
        if not model.is_dir():
            continue
        for case in model.iterdir():
            if not case.is_dir() or not (case.name.endswith('_localize') or case.name in
                    ('dispatch_registry_lto_recover', 'dispatch_registry_optimized_recover')):
                continue
            candidates.extend(sorted(case.glob('*/*/task.json')))
    revisions, excluded = {}, []
    for path in candidates:
        directory = path.parent
        # Only immutable selected, completed grades. No in-flight, stopped,
        # interrupted or incomplete inference is admitted by this operation.
        if not (directory / 'selected-grade.json').exists():
            continue
        captured = {}
        def read(p):
            raw = Path(p).read_bytes()
            key = str(Path(p).relative_to(ROOT))
            if key in captured and captured[key] != sha(raw):
                raise ValueError('Source changed during offline regrade')
            captured[key] = sha(raw)
            return raw
        relative = str(directory.relative_to(ROOT))
        try:
            task = json.loads(read(path))
            if task.get('offline_fixture') or task.get('kind') not in ('localization', 'optimized_localization'):
                continue
            reader = measurements.Reader(ROOT, read)
            observation = measurements.observe(reader, relative, task, models, settings, False)
            if not observation['record_valid'] or not observation['grade'] or observation['status'] not in (
                    'graded', 'completed_over_budget', 'completed_queue_held'):
                continue
            # Valid originals already have semantic scores. No unnecessary regrade.
            if observation['grade']['artifact_valid']:
                continue
            answer = read(directory / 'answer.md')
            private = store.spec(task)
            read(ROOT / task['grading_spec'])
            name = next(iter(task['artifact_schema']))
            normalized, changes, grade = None, [], None
            reason = 'format_not_losslessly_convertible'
            result = None
            try:
                artifact = extract(answer, task['artifact_schema'])[name]
                normalized, changes = normalize(artifact, task['kind'])
            except (ValueError, UnicodeError):
                pass
            if normalized is not None:
                # Same explicit startup policy as the live benchmark. The closed
                # worker still verifies its image, source and frozen target.
                with startup_policy.scope('trusted_local'):
                    result, _ = submission.evaluate(b'```' + name.encode() + b'\n' + normalized + b'\n```\n', task, private)
                if result['artifact_valid'] and result.get('status') != 'wrong_binary':
                    reason = 'semantic_regraded'
                else:
                    reason = 'target_or_contract_not_admitted'
            safe = measurements.public_projection({**measurements.empty(), 'history': [observation]})['history'][0]
            # Bind scoring code using the worker's genuine source digest record;
            # capture normalizer bytes separately from the unchanged old answer.
            normalizer = Path(__file__).read_bytes()
            sources = {'normalizer.py': normalizer}
            runtime = (result or {}).get('grading_runtime', {})
            for source, digest in runtime.get('source_inputs_sha256', {}).items():
                source_raw = (ROOT / source).read_bytes()
                if sha(source_raw) != digest:
                    raise ValueError('Scorer changed during offline regrade')
                sources['source-' + digest + '.py'] = source_raw
            identity = {'policy': POLICY, 'attempt_id': task['fingerprint'],
                        'original_grading_id': observation['grade']['grading_id'],
                        'inputs_sha256': dict(sorted(captured.items())),
                        'normalized_sha256': sha(normalized) if normalized is not None else None,
                        'evaluation_id': (result or {}).get('evaluation_id'), 'reason': reason,
                        'normalizer_sha256': sha(normalizer),
                        'scoring_sources_sha256': runtime.get('source_inputs_sha256', {})}
            revision_id = sha(canonical(identity))
            if reason == 'semantic_regraded':
                grade = {'grading_id': revision_id, 'grading_version': POLICY,
                         **{k: result[k] for k in ('passed', 'total', 'artifact_valid', 'task_success')},
                         'scores': metrics.score_vector(result, task['capability_ids']),
                         'return_provenance': 'structured_artifact'}
            public = {'revision_id': revision_id, 'original_grading_id': observation['grade']['grading_id'],
                      'answer_sha256': sha(answer), 'normalized_sha256': identity['normalized_sha256'],
                      'transforms': len(changes), 'reason': reason, 'observation': safe, 'grade': grade}
            destination = STORE / 'regrades' / revision_id
            destination.mkdir(parents=True, exist_ok=True)
            outputs = {'original-answer.md': answer, 'transforms.json': canonical(changes), **sources}
            if normalized is not None:
                outputs['normalized-' + name] = normalized
            if result is not None:
                outputs['grade.json'] = canonical(result)
            for name, raw in outputs.items():
                output = destination / name
                if output.exists() and output.read_bytes() != raw:
                    raise ValueError('Append-only localization revision collision')
                if not output.exists():
                    output.write_bytes(raw)
            receipt = {'identity': identity, 'public': public,
                       'outputs_sha256': {k: sha(v) for k, v in outputs.items()},
                       'grading_policy': 'trusted_local; closed data-only worker; no acceptance suite rerun',
                       'model_api_calls': 0}
            raw = canonical(receipt)
            record = destination / 'revision.json'
            if record.exists() and record.read_bytes() != raw:
                raise ValueError('Append-only localization receipt collision')
            if not record.exists():
                record.write_bytes(raw)
            # Ensure no in-flight writes occurred after the initial snapshot.
            if any(sha((ROOT / p).read_bytes()) != h for p, h in captured.items()):
                raise ValueError('Original inputs changed before offline publication')
            previous = revisions.get(task['fingerprint'])
            item = {'revision_id': revision_id, 'sha256': sha(raw)}
            if previous and previous != item:
                raise ValueError('Conflicting duplicate attempt')
            revisions[task['fingerprint']] = item
            print(task['model'], directory.parent.parent.name, reason,
                  str((grade['passed'], grade['total'])) if grade else 'ungraded', flush=True)
        except (ValueError, OSError, KeyError, TypeError) as error:
            excluded.append({'directory': relative, 'error_type': type(error).__name__, 'error': str(error)})
    index = {'policy': POLICY, 'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'revisions': list(revisions.values()), 'excluded': excluded, 'model_api_calls': 0}
    temporary = STORE / 'index.pending.json'
    temporary.write_bytes(canonical(index))
    temporary.replace(STORE / 'index.json')
    print('Revisions:', len(revisions), 'excluded:', len(excluded), flush=True)
    return index


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--regrade', action='store_true')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    if args.regrade:
        regrade()
    if args.publish:
        import public_report
        snapshot = public_report.SNAPSHOT
        raw = snapshot.read_bytes()
        value = public_report.validate(json.loads(raw))
        backup = STORE / ('summary-before-' + sha(raw) + '.json')
        if not backup.exists():
            backup.write_bytes(raw)
        value['localization_regrades'] = load()
        public_report.validate(value)
        temporary = snapshot.with_suffix('.localization.pending.json')
        temporary.write_bytes(canonical(value))
        if snapshot.read_bytes() != raw:
            raise RuntimeError('Report snapshot changed; refusing to replace it')
        temporary.replace(snapshot)
        print('Published verified localization overlay; base report timestamp unchanged.')
