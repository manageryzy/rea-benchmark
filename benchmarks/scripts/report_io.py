"""One fresh, bounded input snapshot per report build (never inference inputs).

The previous read set is only a prefetch hint. Every file is read and hashed
again; collectors still discover new files and missing files normally. Nothing
is trusted because a timestamp, prior report or filename says it is unchanged.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
from unittest.mock import patch

import records


@contextmanager
def metadata_snapshot():
    """Memoize read-only filesystem queries, then freshly verify every answer.

    realpath walks the same ancestors for thousands of report links and Merkle
    blobs. On drvfs those redundant calls dominate rendering. This scope must
    not contain writes; final checks use original syscalls in parallel. No
    metadata is persisted or used as a content/action identity.
    """
    from contextlib import ExitStack
    observations, lock = {}, threading.RLock()
    originals = {name: getattr(os, name) for name in ('stat', 'lstat', 'readlink')}
    def query(name, path, *args, **kwargs):
        if isinstance(path, int) or args or kwargs.get('dir_fd') is not None:
            return originals[name](path, *args, **kwargs)
        key = (name, os.fspath(path), tuple(sorted(kwargs.items())))
        with lock:
            found = observations.get(key)
        # Directory timestamps change when unrelated cache/pycache entries are
        # created. Never replay those timestamps; still seal directory identity.
        if found is not None and found[0] and isinstance(found[1], os.stat_result) and stat.S_ISDIR(found[1].st_mode):
            return originals[name](path, **kwargs)
        if found is None:
            try:
                found = (True, originals[name](path, **kwargs))
            except OSError as error:
                found = (False, (error.errno, error.strerror))
            with lock:
                observations[key] = found
        if not found[0]:
            raise OSError(*found[1], path)
        return found[1]
    def comparable(value):
        if not isinstance(value, os.stat_result):
            return value
        if stat.S_ISDIR(value.st_mode):
            return tuple(getattr(value, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid'))
        return tuple(getattr(value, name) for name in ('st_dev', 'st_ino', 'st_mode',
            'st_nlink', 'st_uid', 'st_gid', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
    def verify(item):
        (name, path, pairs), expected = item
        try:
            observed = (True, originals[name](path, **dict(pairs)))
        except OSError as error:
            observed = (False, (error.errno, error.strerror))
        if observed[0] != expected[0] or comparable(observed[1]) != comparable(expected[1]):
            raise ValueError('Report filesystem changed during read-only phase: ' + str(path))
    with ExitStack() as stack:
        for name in originals:
            stack.enter_context(patch.object(os, name,
                lambda path, *args, _name=name, **kwargs: query(_name, path, *args, **kwargs)))
        yield
        with ThreadPoolExecutor(max_workers=16) as workers:
            list(workers.map(verify, list(observations.items())))


class Snapshot:
    def __init__(self, root=records.ROOT, limit=4 * 1024**3):
        self.root, self.limit = Path(root), limit
        self.values, self.used, self.lock = {}, 0, threading.RLock()
        self.reads = self.hits = 0

    @staticmethod
    def signature(path):
        value = path.stat()
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns,
                value.st_ctime_ns, value.st_mode)

    def read(self, path, *, track=True):
        path = Path(path)
        name = str(path.relative_to(self.root))
        before = self.signature(path)
        with self.lock:
            previous = self.values.get(name)
        if previous is not None and previous[0] == before:
            _, raw, digest = previous
            self.hits += 1
        else:
            raw = path.read_bytes()
            if self.signature(path) != before:
                raise ValueError('Report input changed during read: ' + name)
            digest = hashlib.sha256(raw).hexdigest()
            with self.lock:
                self.reads += 1
                if self.used + len(raw) <= self.limit:
                    self.values[name] = (before, raw, digest)
                    self.used += len(raw) - (len(previous[1]) if previous else 0)
        if track:
            records.INPUTS[name] = digest
        return raw

    def prefetch(self, names):
        def fetch(name):
            # Hints from generated output cannot authorize paths outside root.
            path = Path(name)
            if path.is_absolute() or '..' in path.parts:
                return
            try:
                self.read(self.root / path, track=False)
            except OSError:
                pass  # Missing inputs are handled by the actual collector.
        with ThreadPoolExecutor(max_workers=16) as workers:
            list(workers.map(fetch, names))

    def seal(self):
        """A fresh content check prevents publishing a mixed/stale snapshot."""
        def verify(item):
            name, expected = item
            raw = (self.root / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != expected:
                raise ValueError('Report input changed during collection: ' + name)
        with ThreadPoolExecutor(max_workers=16) as workers:
            list(workers.map(verify, list(records.INPUTS.items())))


@contextmanager
def snapshot(previous_inputs):
    value = Snapshot()
    start = time.perf_counter()
    try:
        hints = json.loads(Path(previous_inputs).read_bytes()).get('files_sha256', {})
    except (OSError, ValueError):
        hints = {}
    value.prefetch(hints)
    print(f'REPORT TIMING: prefetch {time.perf_counter() - start:.3f}s ({len(hints)} hints)', flush=True)
    with patch.object(records, 'read_bytes', value.read):
        yield value


@contextmanager
def timed(name, rows):
    start = time.perf_counter()
    print('REPORT STAGE:', name, flush=True)
    try:
        yield
    finally:
        seconds = time.perf_counter() - start
        rows.append({'stage': name, 'seconds': round(seconds, 6)})
        print(f'REPORT TIMING: {name} {seconds:.3f}s', flush=True)


def publish_timing(seconds=None):
    """Small operational overlay, reproducible from saved numeric receipts."""
    from html import escape
    root = records.ROOT
    target = root / 'benchmarks/config/benchmark-command-timing.json'
    status_path = root / 'benchmarks/config/benchmark-status.json'
    if seconds is not None:
        status = json.loads(status_path.read_bytes())
        value = {'schema_version': 1, 'seconds': round(seconds, 6),
                 'mode': 'offline' if status.get('offline') else 'live',
                 'selected': status.get('selected_tasks', 0), 'cache_hits': status.get('cache_hits', 0),
                 'failures': len(status.get('failures', [])),
                 'unprocessed': status.get('unprocessed_tasks', 0),
                 'model_api_calls': status.get('model_api_calls'),
                 'stages': status.get('timings', {}).get('stages', [])}
        # Only numeric/status data, never error text, selections or model traces.
        value['stages'] = [{k: row[k] for k in ('stage', 'status', 'elapsed_seconds')}
                           for row in value['stages']]
        target.write_text(json.dumps(value, indent=2) + '\n')
    if not target.exists():
        return
    value = json.loads(target.read_bytes())
    timing_path = root / 'benchmarks/config/report-timings.json'
    report = json.loads(timing_path.read_bytes()) if timing_path.exists() else {}
    def number(v):
        return 'unknown' if v is None else f'{float(v):,.3f}'
    rows = [(row['stage'], row['elapsed_seconds']) for row in value['stages']]
    rows += [('report / ' + row['stage'], row['seconds']) for row in report.get('stages', [])]
    body = ('<!doctype html><html lang="en"><meta charset="utf-8"><title>Benchmark pipeline timing</title>'
            '<link rel="stylesheet" href="assets/report.css"><main><p><a href="index.html">Global report</a>'
            ' · <a href="diagnostics.html">Diagnostics</a></p><h1>Benchmark pipeline timing</h1>'
            '<p>Coordinator command wall time, including report generation: <strong>' + number(value['seconds']) +
            ' seconds</strong>. Mode: ' + escape(value['mode']) + '.</p><p>Selected: ' + str(int(value['selected'])) +
            '; cache hits: ' + str(int(value['cache_hits'])) + '; unavailable or rejected: ' + str(int(value['failures'])) +
            '; unprocessed: ' + str(int(value['unprocessed'])) + '; model API calls: ' + number(value['model_api_calls']) +
            '.</p><p>Unavailable caches are not correctness failures. Report subtimings are included in finalization; '
            'do not add them twice. The command clock excludes Make process startup and this small timing-page write.</p>'
            '<table><thead><tr><th>Stage</th><th>Seconds</th></tr></thead><tbody>' +
            ''.join('<tr><td>' + escape(name) + '</td><td>' + number(elapsed) + '</td></tr>' for name, elapsed in rows) +
            '</tbody></table></main></html>\n')
    for directory in ('benchmarks/reports', 'benchmarks/site', 'docs'):
        output = root / directory
        if (output / 'index.html').is_file():
            (output / 'timings.html').write_text(body)
            diagnostic = output / 'diagnostics.html'
            if diagnostic.exists():
                text = diagnostic.read_text()
                link = '<p id="pipeline-timing-link"><a href="timings.html">Full benchmark command timing</a></p>'
                if 'id="pipeline-timing-link"' not in text:
                    diagnostic.write_text(text.replace('</main>', link + '</main>'))
