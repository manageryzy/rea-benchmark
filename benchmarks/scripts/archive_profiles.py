#!/usr/bin/python3
"""Preserve exact benchmark source profiles without changing saved attempts."""
import hashlib
import json
from pathlib import Path

import records

ROOT, BENCH = records.ROOT, records.BENCH
OUTPUT = BENCH / 'config/environment-profile-archives.json'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    known = {}
    for path in sorted((BENCH / 'runs').glob('*/conditions/*/profiles/*/profile.json')):
        value = json.loads(path.read_text())
        profile = value.get('environment_profile', value)
        fingerprint = profile.get('fingerprint')
        if fingerprint:
            known[fingerprint] = path.parent
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
        for name, expected in profile['files_sha256'].items():
            source = ROOT / name
            assert source.resolve().is_relative_to(BENCH), 'Unexpected profile source'
            destination = archive / name
            if not destination.exists():
                raw = source.read_bytes()
                assert digest(raw) == expected, 'Actual source version is missing; refuse to reconstruct it: ' + name
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
                                 'files_sha256': files, 'attempt_tasks': [str(task_path.relative_to(ROOT))]}
    temporary = OUTPUT.with_suffix('.tmp')
    temporary.write_text(json.dumps({'schema_version': 1, 'model_api_calls': 0,
        'scope': 'Exact source/configuration bytes for the saved fixed-harness task profiles',
        'profiles': profiles}, sort_keys=True, indent=2) + '\n')
    temporary.replace(OUTPUT)
    print(f'Archived {len(profiles)} exact source profiles across {len(tasks)} saved task inputs; zero model calls')


if __name__ == '__main__':
    main()
