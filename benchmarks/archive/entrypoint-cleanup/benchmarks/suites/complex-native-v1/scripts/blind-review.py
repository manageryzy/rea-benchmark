"""Export source-only review labels; keep the model mapping out of review files."""

import hashlib
import json
from pathlib import Path
import random
import shutil
import sys

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]


def main(run_id):
    cohort = BENCH / "runs" / run_id
    manifest = json.loads((cohort / "run-manifest.json").read_text())
    directory = cohort / "blind-review"
    directory.mkdir(exist_ok=True)
    key_path = cohort / "blind-review-key.json"
    if key_path.exists():
        raise SystemExit('Blind labels already exist; preserve the existing review mapping.')
    candidates = []
    for language, model in manifest["configuration"]["run_order"]:
        extension = {"cpp": "cpp", "rust": "rs", "go": "go"}[language]
        source = cohort / model / language / f"reconstructed.{extension}"
        if not source.exists():
            raise SystemExit(f"Missing completed source for {language}; finish grading before review")
        candidates.append((language, model, source, extension))
    random.SystemRandom().shuffle(candidates)
    mapping = {}
    for index, (language, model, source, extension) in enumerate(candidates):
        label = chr(ord('A') + index)
        shutil.copy2(source, directory / f"{label}.{extension}")
        mapping[label] = {"language": language, "model": model, "file": source.name,
                          "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
    key_path.write_text(json.dumps(mapping, indent=2) + "\n")
    (directory / "rubric.json").write_text(json.dumps(manifest["configuration"]["readability_rubric"], indent=2) + "\n")
    print(f"Exported {len(mapping)} labelled sources in {directory}; mapping kept separately.")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "complex-pilot-common-budget-20261019")
