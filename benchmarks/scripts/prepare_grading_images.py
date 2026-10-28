"""Prepare reusable grading images once; no tests or model calls."""
import argparse
import json
import time
from grading import runtime, image_manifest
from read_session import ReadSession


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--languages', nargs='+', choices=('c', 'cpp', 'rust', 'go'), default=['c', 'cpp', 'rust', 'go'])
    args = parser.parse_args()
    with ReadSession():
        for language in args.languages:
            started = time.monotonic()
            record = image_manifest.load(language)
            root = runtime.prepared_image(record)
            print(json.dumps({'language': language, 'image': record['id'], 'path': str(root),
                'seconds': round(time.monotonic()-started, 3), 'model_api_calls': 0}), flush=True)


if __name__ == '__main__':
    main()
