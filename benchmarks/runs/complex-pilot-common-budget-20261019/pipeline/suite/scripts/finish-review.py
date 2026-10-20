"""Attach committed source-only ratings after revealing the saved model mapping."""

import datetime
import hashlib
import json
from pathlib import Path
import sys

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]


def main(run_id):
    cohort = BENCH / "runs" / run_id
    ratings_path = cohort / "blind-review/ratings.json"
    ratings = json.loads(ratings_path.read_text())
    key = json.loads((cohort / "blind-review-key.json").read_text())
    rubric = json.loads((cohort / "run-manifest.json").read_text())["configuration"]["readability_rubric"]
    assert set(ratings) == set(key)
    reviews = {}
    for label, rating in ratings.items():
        metadata = key[label]
        scores = rating["scores"]
        assert set(scores) == set(rubric["dimensions"])
        assert all(isinstance(value, int) and 0 <= value <= 2 for value in scores.values())
        assert rating["notes"].strip()
        source = cohort / metadata["model"] / metadata["language"] / metadata["file"]
        assert hashlib.sha256(source.read_bytes()).hexdigest() == metadata["source_sha256"]
        reviews[f'{metadata["model"]}/{metadata["language"]}'] = {
            **rating, "total": sum(scores.values()), "blind_label": label,
            "candidate_source_sha256": metadata["source_sha256"],
        }
    result = {"reviewer": rubric["reviewer"], "rubric": rubric,
              "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "ratings_committed_before_unblinding": True,
              "ratings_sha256": hashlib.sha256(ratings_path.read_bytes()).hexdigest(),
              "reviews": reviews}
    destination = cohort / "readability-reviews.json"
    with destination.open("x") as output:
        output.write(json.dumps(result, indent=2) + "\n")
    print(f"Attached {len(reviews)} committed readability reviews; source hashes verified. Missing submissions have no readability score.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "complex-pilot-common-budget-20261019")
