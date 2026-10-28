"""Pin one existing compiler/Python image for the lifetime of a grading job."""
from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import threading

from grading import image_manifest
from grading import runtime as images

_lock = threading.RLock()
_record = None
_image = None


def record():
    global _record
    with _lock:
        selected = json.loads((image_manifest.CACHE / 'active.json').read_bytes()).get('c')
        policy_sha = image_manifest.digest(image_manifest.policy('c'))
        if not selected or selected.get('policy_sha256') != policy_sha:
            raise RuntimeError('C compiler image is not prepared; run make setup-c-executor')
        if _record is None:
            _record = image_manifest.load('c')
        if selected['id'] != _record['id'] or policy_sha != _record['manifest']['policy_sha256']:
            raise RuntimeError('Compiler image changed during this grading job; start a fresh job')
        return _record


@contextmanager
def materialize():
    global _image
    selected = record()
    import startup_policy
    if startup_policy.trusted():
        yield images.prepared_image(selected), selected
        return
    with _lock:
        if _image is None:
            temporary = tempfile.TemporaryDirectory(prefix='rea-c-base-', dir='/tmp')
            root = Path(temporary.name) / 'root'
            try:
                images.extract(selected, root)
                signature = images.image_signature(root)
            except BaseException:
                temporary.cleanup()
                raise
            _image = (temporary, root, signature)
        else:
            temporary, root, signature = _image
        if images.image_signature(root) != signature:
            raise ValueError('Pinned compiler/Python image changed before C execution')
    try:
        yield root, selected
    finally:
        if images.image_signature(root) != signature:
            raise ValueError('Pinned compiler/Python image changed during C execution')
