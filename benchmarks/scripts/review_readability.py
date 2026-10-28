#!/usr/bin/python3
"""Prepare source-only review files and attach explicit rubric decisions; no model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import os
import tempfile

import records

DIMENSIONS = ['formatting', 'names', 'decomposition', 'language_idioms', 'explanatory_comments']
PACK = records.ROOT / '.cache/benchmark-runtime/readability'


def candidates():
    return [r for r in records.collect()['condition_records'] if r['source']]


def prepare(*, reassess=False):
    PACK.mkdir(parents=True, exist_ok=True)
    output = {}
    for row in candidates():
        source = records.ROOT / row['source']
        raw = source.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        path = PACK / (digest + source.suffix)
        if path.exists():
            assert path.read_bytes() == raw
        else:
            path.write_bytes(raw)
        if reassess or not (source.parent / 'readability-review.json').exists():
            output[digest] = {'source_sha256': digest, 'file': str(path), 'bytes': len(raw), 'language': row['case']['language']}
    print(json.dumps({'dimensions': DIMENSIONS, 'pending': list(output.values())}, indent=2))


def store_review(destination, review, *, reassess=False):
    """Explicit reassessment archives exact prior bytes before replacing selection."""
    raw = (json.dumps(review, indent=2) + '\n').encode()
    if destination.exists():
        old = destination.read_bytes()
        if json.loads(old) == review:
            return False
        assert reassess, 'Existing source review is immutable; use explicit --reassess'
        history = destination.parent / 'readability-history'
        history.mkdir(exist_ok=True)
        archive = history / (hashlib.sha256(old).hexdigest() + '.json')
        if archive.exists():
            assert archive.read_bytes() == old, 'Archived readability review changed'
        else:
            with archive.open('xb') as stream:
                stream.write(old)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination.parent, prefix='.readability-', delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def apply(path, *, reassess=False):
    decisions = json.loads(path.read_text())
    indexed = {d['source_sha256']: d for d in decisions}
    assert len(indexed) == len(decisions), 'Duplicate source decisions'
    known = candidates()
    assert not (set(indexed) - {records.sha(records.ROOT / r['source']) for r in known}), 'Decision source hash does not match an accepted candidate'
    count = 0
    for row in known:
        source = records.ROOT / row['source']
        digest = records.sha(source)
        if digest not in indexed:
            continue
        decision = indexed[digest]
        scores = decision['scores']
        assert set(scores) == set(DIMENSIONS)
        assert all(type(v) is int and 0 <= v <= 2 for v in scores.values())
        review = {'candidate_source_sha256': digest, 'scores': scores, 'total': sum(scores.values()),
                  'notes': decision['notes'], 'reviewer': 'Coordinating AI, source-only rubric review',
                  'model_labels_in_review_pack': False, 'independent_review': False,
                  'coordinator_has_run_context': True}
        destination = source.parent / 'readability-review.json'
        count += store_review(destination, review, reassess=reassess)
    print(f'Attached {count} explicit source-only reviews; no inferred scores or model calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply'])
    parser.add_argument('--decisions', type=Path)
    parser.add_argument('--reassess', action='store_true', help='Include reviewed sources / archive and replace prior explicit reviews')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(reassess=args.reassess)
    else:
        assert args.decisions, '--decisions is required'
        apply(args.decisions, reassess=args.reassess)
