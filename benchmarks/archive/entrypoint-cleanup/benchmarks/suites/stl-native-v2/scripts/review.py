"""Source-only labels and committed readability ratings, with missing code explicit."""
import datetime
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys

SUITE = Path(__file__).resolve().parents[1]
COHORT = SUITE.parents[1] / 'runs/stl-rust-data-v2-20261019'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export():
    manifest = json.loads((COHORT / 'run-manifest.json').read_text())
    if (COHORT / 'blind-review-key.json').exists():
        print('CACHE HIT: preserve existing source labels; do not reshuffle')
        return
    candidates, missing = [], []
    for case, model in manifest['configuration']['run_order']:
        directory = COHORT / model / case
        if not (directory / 'completed.json').exists(): raise RuntimeError('Finish all first attempts before exporting sources')
        extension = 'rs' if case.startswith('rust') else 'cpp'
        source = directory / f'reconstructed.{extension}'
        if source.exists(): candidates.append((case, model, source, extension))
        else: missing.append({'case_id': case, 'model': model, 'reason': 'No native candidate source; readability not scored'})
    random.SystemRandom().shuffle(candidates)
    directory = COHORT / 'blind-review'
    directory.mkdir(exist_ok=True)
    key = {}
    for index, (case, model, source, extension) in enumerate(candidates):
        label = chr(ord('A') + index)
        shutil.copy2(source, directory / f'{label}.{extension}')
        key[label] = {'case_id': case, 'model': model, 'extension': extension, 'source_sha256': digest(source)}
    (COHORT / 'blind-review-key.json').write_text(json.dumps(key, indent=2) + '\n')
    (directory / 'rubric.json').write_text(json.dumps(manifest['configuration']['readability_rubric'], indent=2) + '\n')
    (COHORT / 'missing-readability.json').write_text(json.dumps(missing, indent=2) + '\n')
    print(f'Exported {len(candidates)} source-only labels; {len(missing)} missing submissions')


def finish():
    ratings_path = COHORT / 'blind-review/ratings.json'
    commit_path = COHORT / 'blind-review/ratings-commit.json'
    commit = json.loads(commit_path.read_text())
    assert commit['ratings_sha256'] == digest(ratings_path)
    ratings = json.loads(ratings_path.read_text())
    key = json.loads((COHORT / 'blind-review-key.json').read_text())
    assert set(ratings) == set(key)
    dimensions = json.loads((COHORT / 'blind-review/rubric.json').read_text())['dimensions']
    reviews = {}
    for label, rating in ratings.items():
        assert set(rating['scores']) == set(dimensions)
        assert all(type(score) is int and 0 <= score <= 2 for score in rating['scores'].values())
        assert rating['notes'].strip()
        meta = key[label]
        source = COHORT / meta['model'] / meta['case_id'] / f'reconstructed.{meta["extension"]}'
        assert digest(source) == meta['source_sha256']
        reviews[f'{meta["model"]}/{meta["case_id"]}'] = {**rating, 'total': sum(rating['scores'].values()),
            'label': label, 'candidate_source_sha256': meta['source_sha256']}
    result = {'reviewer': 'Coordinating AI; model labels removed from source-only review files',
        'reviewer_type': 'Subjective analyst assessment, not an independent human panel',
        'ratings_committed_before_mapping_read': True, 'rating_commit': commit, 'reviews': reviews,
        'missing': json.loads((COHORT / 'missing-readability.json').read_text()),
        'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
    with (COHORT / 'readability-reviews.json').open('x') as output: output.write(json.dumps(result, indent=2) + '\n')
    print(f'Attached {len(reviews)} hash-verified reviews; no candidate edits')


if __name__ == '__main__':
    {'export': export, 'finish': finish}[sys.argv[1]]()
