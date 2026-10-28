"""Host facts observed independently by the coordinator and sealed workers."""
import hashlib
import json
import os
from pathlib import Path


def host():
    fields = {'vendor_id', 'cpu family', 'model', 'model name', 'stepping', 'flags', 'Features', 'CPU architecture'}
    records = set()
    for block in Path('/proc/cpuinfo').read_text().split('\n\n'):
        values = {}
        for line in block.splitlines():
            if ':' in line:
                key, value = (part.strip() for part in line.split(':', 1))
                if key in fields:
                    values[key] = value
        if values:
            records.add(json.dumps(values, sort_keys=True))
    return {'platform': os.uname().machine, 'kernel': os.uname().release,
            'cpu_capabilities_sha256': hashlib.sha256(json.dumps(sorted(records)).encode()).hexdigest()}
