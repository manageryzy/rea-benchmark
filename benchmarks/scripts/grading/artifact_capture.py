"""Bounded exact Markdown artifact transport; semantic validation is separate."""
import re

MAX_ANSWER = 2 * 1024**2


def extract(raw, contract):
    if not isinstance(raw, bytes) or len(raw) > MAX_ANSWER:
        raise ValueError('Submission answer exceeds its byte limit')
    raw.decode('utf-8')
    files, current, chunks = {}, None, []
    for line in raw.splitlines(keepends=True):
        marker = re.fullmatch(rb'```([^\r\n`]*)\r?\n?', line)
        if marker:
            name = marker[1].decode('ascii')
            if current is None:
                if name not in contract or name in files:
                    raise ValueError('Unknown, duplicate or unnamed artifact block')
                current, chunks = name, []
            elif name:
                raise ValueError('Nested artifact fences are invalid')
            else:
                files[current], current = b''.join(chunks), None
        elif current is not None:
            chunks.append(line)
    if current is not None or set(files) != set(contract):
        raise ValueError('Missing or unfinished artifact block')
    return files
