#!/usr/bin/python3
"""Preserve exact benchmark source profiles without changing saved attempts."""
import hashlib
import json
from pathlib import Path

import records

ROOT, BENCH = records.ROOT, records.BENCH
OUTPUT = BENCH / 'config/environment-profile-archives.json'


class MissingSource(AssertionError):
    pass


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def exact_source(name, expected, *, root=None, archived=()):
    """Recover bytes, never reconstruct a historical source from newer code."""
    root = root or ROOT
    source = root / name
    assert source.resolve().is_relative_to(root / 'benchmarks'), 'Unexpected profile source'
    assert len(expected) == 64 and all(c in '0123456789abcdef' for c in expected)
    candidates = [source, root / '.cache/benchmark-actions/v3/cas' / expected, *archived]
    for candidate in candidates:
        if candidate.is_file():
            raw = candidate.read_bytes()
            if digest(raw) == expected:
                return raw
    raise MissingSource('Actual source version is missing; refuse to reconstruct it: ' + name)


def main():
    known = {}
    archived = {}
    for path in sorted((BENCH / 'runs').glob('*/conditions/*/profiles/*/profile.json')):
        value = json.loads(path.read_text())
        profile = value.get('environment_profile', value)
        fingerprint = profile.get('fingerprint')
        if fingerprint:
            known[fingerprint] = path.parent
        for name, expected in profile.get('files_sha256', {}).items():
            archived.setdefault((name, expected), []).append(path.parent / name)
    profiles = {}
    tasks = sorted((BENCH / 'runs').glob('*/conditions/*/*/*/task.json'))
    for task_path in tasks:
        task = json.loads(task_path.read_text())
        profile = task.get('environment_profile')
        if not profile:
            continue
        fingerprint = profile['fingerprint']
        if fingerprint in profiles:
            profiles[fingerprint]['attempt_tasks'].append(str(task_path.relative_to(ROOT)))
            continue
        identity = {k: v for k, v in profile.items() if k != 'fingerprint'}
        assert digest(json.dumps(identity, sort_keys=True).encode()) == fingerprint
        archive = known.get(fingerprint, task_path.parents[2] / 'profiles' / fingerprint)
        archive.mkdir(parents=True, exist_ok=True)
        files = {}
        missing = {}
        for name, expected in profile['files_sha256'].items():
            source = ROOT / name
            assert source.resolve().is_relative_to(BENCH), 'Unexpected profile source'
            destination = archive / name
            if not destination.exists():
                try:
                    raw = exact_source(name, expected, archived=archived.get((name, expected), ()))
                except MissingSource:
                    # A missing historical source is an evidence gap, not a
                    # reason to discard current results or fabricate bytes.
                    # This reporting archive is never an admission receipt.
                    missing[name] = expected
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open('xb') as stream:
                    stream.write(raw)
            assert digest(destination.read_bytes()) == expected, 'Archived source changed: ' + str(destination)
            files[str(destination.relative_to(ROOT))] = expected
        manifest = archive / 'profile.json'
        if not manifest.exists():
            manifest.write_text(json.dumps(profile, sort_keys=True, indent=2) + '\n')
        files[str(manifest.relative_to(ROOT))] = digest(manifest.read_bytes())
        profiles[fingerprint] = {'profile': profile, 'directory': str(archive.relative_to(ROOT)),
                                 'files_sha256': files, 'missing_files_sha256': missing,
                                 'complete': not missing, 'attempt_tasks': [str(task_path.relative_to(ROOT))]}
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps({'schema_version': 1, 'model_api_calls': 0,
        'scope': 'Exact source/configuration bytes for the saved fixed-harness task profiles',
        'profiles': profiles}, sort_keys=True, indent=2) + '\n')
    temporary.replace(OUTPUT)
    incomplete = sum(not p['complete'] for p in profiles.values())
    print(f'Archived {len(profiles) - incomplete} complete source profiles; {incomplete} explicitly incomplete historical profiles across {len(tasks)} saved task inputs; zero model calls')


if __name__ == '__main__':
    main()
