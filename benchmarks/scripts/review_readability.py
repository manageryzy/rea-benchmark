#!/usr/bin/python3
"""Prepare source-only review files and attach explicit rubric decisions; no model calls."""
import argparse
import hashlib
import json
from pathlib import Path

import records

DIMENSIONS = ['formatting', 'names', 'decomposition', 'language_idioms', 'explanatory_comments']
PACK = records.ROOT / '.cache/benchmark-runtime/readability'


def candidates():
    return [r for r in records.collect()['condition_records'] if r['source']]


def prepare():
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
        if not (source.parent / 'readability-review.json').exists():
            output[digest] = {'source_sha256': digest, 'file': str(path), 'bytes': len(raw), 'language': row['case']['language']}
    print(json.dumps({'dimensions': DIMENSIONS, 'pending': list(output.values())}, indent=2))


def apply(path):
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
        if destination.exists():
            assert json.loads(destination.read_text()) == review, 'Existing source review is immutable'
        else:
            destination.write_text(json.dumps(review, indent=2) + '\n')
            count += 1
    print(f'Attached {count} explicit source-only reviews; no inferred scores or model calls')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply'])
    parser.add_argument('--decisions', type=Path)
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
    else:
        assert args.decisions, '--decisions is required'
        apply(args.decisions)
