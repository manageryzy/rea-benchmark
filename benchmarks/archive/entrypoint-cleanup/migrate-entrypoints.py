from pathlib import Path
import hashlib,json,shutil
root=Path.cwd();archive=root/'benchmarks/archive/entrypoint-cleanup'
names=['benchmarks/scripts/batch.py','benchmarks/config/batches.json','benchmarks/scripts/audit.py',
 'benchmarks/suites/record-parser/scripts/run.mjs',
 'benchmarks/suites/complex-native-v1/scripts/run.mjs','benchmarks/suites/complex-native-v1/scripts/audit.py',
 'benchmarks/suites/complex-native-v1/scripts/blind-review.py','benchmarks/suites/complex-native-v1/scripts/finish-review.py',
 'benchmarks/suites/stl-native-v2/scripts/run.mjs','benchmarks/suites/stl-native-v2/scripts/audit.py',
 'benchmarks/suites/stl-native-v2/scripts/review.py']
items={}
for name in names:
 src=root/name;dst=archive/name;dst.parent.mkdir(parents=True,exist_ok=True)
 data=src.read_bytes();dst.write_bytes(data);src.unlink()
 items[name]={'archive':str(dst.relative_to(root)),'sha256':hashlib.sha256(data).hexdigest()}
(archive/'manifest.json').write_text(json.dumps({'schema_version':1,'note':'Historical sources only; not executable entrypoints. Frozen run/profile archives remain in place.','files':items},sort_keys=True,indent=2)+'\n')
(archive/'README.md').write_text('''# Historical entrypoints and cohort helpers

These byte-for-byte sources document earlier execution, audit and source-review workflows.
They are archived reference material; their original relative imports and hardcoded cohorts
are intentionally preserved. Do not execute them from this directory.

Use `make benchmark` (all enabled models and active cases), `make benchmark-plan`,
`make reports`, `make check`, `make audit-condition`, and `make review-prepare` instead.
Selection and scheduling live in `benchmarks/config/benchmark.json`. `manifest.json`
records every move and original SHA-256. All raw runs, grades, reviews, frozen inputs
and exact source-profile archives remain at their recorded locations.
''')
print('Archived',len(items),'historical entrypoints and cohort helpers')
