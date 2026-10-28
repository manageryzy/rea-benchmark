"""Exact return records. Native frames preserve bytes, not return provenance."""
import re

PROTOCOL = 'numbered-length-hex-v2'
PREFIX = b'__REA_RETURN_V2__:'
FRAME = re.compile(rb'__REA_RETURN_V2__:(0|[1-9][0-9]*):ok:(bytes|int):(0|[1-9][0-9]*):([0-9a-f]*)\n')


def encode(sequence, value, kind='bytes'):
    if kind not in ('bytes', 'int') or type(sequence) is not int or sequence < 0:
        raise ValueError('Invalid return record')
    if kind == 'int':
        if type(value) is not int:
            raise ValueError('Integer return required')
        value = str(value).encode('ascii')
    if not isinstance(value, bytes):
        raise ValueError('Byte return required')
    return PREFIX + f'{sequence}:ok:{kind}:{len(value)}:{value.hex()}\n'.encode('ascii')


def expected_bytes(case, standard=None):
    value = case['expected']
    if isinstance(value, dict):
        value = value[standard]
    if type(value) is int:
        return 'int', str(value).encode('ascii')
    if isinstance(value, str):
        return 'bytes', value.encode('utf-8')
    if isinstance(value, bytes):
        return 'bytes', value
    raise ValueError('Unsupported oracle value')


def compare(output, cases, standard=None):
    """IDs may arrive in any order, exactly once. Noise is never a return value."""
    frames, invalid, noise, errors = {}, set(), {}, []
    previous = 0
    for match in FRAME.finditer(output):
        # Bound numeric conversion independently of Python's integer digit limit.
        if len(match[1]) > 12 or len(match[3]) > 12:
            errors.append('oversized_record_field')
            continue
        index, length = int(match[1]), int(match[3])
        preceding = output[previous:match.start()]
        previous = match.end()
        if preceding:
            noise[index] = noise.get(index, b'') + preceding
            errors.append('unexpected_stdout')
        if index in frames:
            invalid.add(index)
            errors.append('duplicate_return_id')
        kind = match[2].decode('ascii')
        raw = match[4]
        if len(raw) % 2:
            value = None
        else:
            value = bytes.fromhex(raw.decode('ascii'))
        if value is None or len(value) != length:
            invalid.add(index)
            errors.append('invalid_return_length')
        if kind == 'int' and (value is None or not re.fullmatch(rb'(?:0|-?[1-9][0-9]*)', value)):
            invalid.add(index)
            errors.append('invalid_integer_value')
        frames[index] = (kind, value)
    tail = output[previous:]
    if tail:
        errors.append('unexpected_stdout')
    wanted = set(range(len(cases)))
    if wanted - frames.keys():
        errors.append('missing_return_ids')
    if frames.keys() - wanted:
        errors.append('extra_return_ids')
    actual = []
    for index, case in enumerate(cases):
        expected = expected_bytes(case, standard)
        value = frames.get(index)
        matches = value == expected
        actual.append({'sequence': index, 'passed': matches and index not in invalid and not noise.get(index),
                       'value_matches': matches, 'returned_hex': value[1].hex() if value and value[1] is not None else None,
                       'expected_hex': expected[1].hex(), 'unexpected_stdout_bytes': len(noise.get(index, b''))})
    return {'protocol': PROTOCOL, 'records': actual, 'errors': sorted(set(errors)),
            'returned_frames': len(frames), 'unexpected_stdout_tail_bytes': len(tail),
            'return_provenance': 'native_process_unauthenticated'}
