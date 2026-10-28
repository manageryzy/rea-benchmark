"""Strict, bounded submission artifacts; no inference or language-code extraction.

Structured artifacts are exact UTF-8 JSON documents. Duplicate keys, floats,
non-finite numbers, unknown fields and path-like artifact names fail admission.
Admission proves shape, not semantic correctness or ABI compatibility.
"""
import hashlib
import json
import re

MAX_BYTES = 262144
IDENTIFIER = re.compile(r'[A-Za-z_][A-Za-z0-9_]{0,63}\Z')
DIGEST = re.compile(r'[0-9a-f]{64}\Z')


def object_fields(value, required, optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional) or set(required) - set(value):
        raise ValueError('Missing or unknown artifact fields')
    return value


def integer(value, minimum=0, maximum=2**64 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError('Artifact integer is outside its bounds')
    return value


def identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('Invalid artifact identifier')
    return value


def digest(value):
    if not isinstance(value, str) or not DIGEST.fullmatch(value):
        raise ValueError('Invalid artifact binary digest')
    return value


def sequence(value, maximum):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError('Artifact list exceeds its bound')
    return value


def unique(value):
    keys = [json.dumps(item, sort_keys=True, separators=(',', ':')) for item in value]
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate artifact entries')


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate JSON key')
        result[key] = value
    return result


def _no_float(value):
    raise ValueError('Artifact JSON must use integer values, not floating point')


def decode(raw):
    if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_BYTES:
        raise ValueError('Artifact must contain bounded exact bytes')
    try:
        return json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs,
                          parse_float=_no_float, parse_constant=_no_float)
    except (UnicodeError, RecursionError, OverflowError) as error:
        raise ValueError('Invalid or excessively nested artifact JSON') from error


def _range(value, maximum=64):
    object_fields(value, ('start', 'end'))
    start, end = integer(value['start']), integer(value['end'])
    if not 1 <= end - start <= maximum:
        raise ValueError('Invalid or excessively broad instruction range')


def locations(value):
    object_fields(value, ('schema_version', 'binary_sha256', 'status', 'roots', 'sites',
                          'dispatch', 'references', 'chain'))
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise ValueError('Unknown locations schema')
    digest(value['binary_sha256'])
    if value['status'] not in ('present', 'absent', 'unanswered'):
        raise ValueError('Unknown target-presence status')
    for root in sequence(value['roots'], 3):
        integer(root)
    unique(value['roots'])
    sites = sequence(value['sites'], 16)
    for site in sites:
        object_fields(site, ('role', 'start', 'end'))
        identifier(site['role'])
        _range({key: site[key] for key in ('start', 'end')})
    if sum(site['end'] - site['start'] for site in sites) > 256:
        raise ValueError('Submitted instruction coverage is too broad')
    unique(sites)
    for edge in sequence(value['dispatch'], 24):
        object_fields(edge, ('condition', 'site', 'target'))
        identifier(edge['condition'])
        integer(edge['site'])
        integer(edge['target'])
    unique(value['dispatch'])
    for reference in sequence(value['references'], 24):
        object_fields(reference, ('site', 'object', 'length', 'access'))
        integer(reference['site'])
        integer(reference['object'])
        integer(reference['length'], 1, 4096)
        if reference['access'] not in ('read', 'write', 'read_write', 'address'):
            raise ValueError('Unknown data-access role')
    unique(value['references'])
    chain = value['chain']
    if chain is not None:
        object_fields(chain, ('root', 'nodes', 'edges'))
        integer(chain['root'])
        for node in sequence(chain['nodes'], 16):
            integer(node)
        unique(chain['nodes'])
        if chain['root'] not in chain['nodes']:
            raise ValueError('Chain root must belong to its nodes')
        for edge in sequence(chain['edges'], 24):
            if not isinstance(edge, list) or len(edge) != 2 or any(type(node) is not int or node not in chain['nodes'] for node in edge):
                raise ValueError('Chain edge must name submitted nodes')
        unique(chain['edges'])
    if value['status'] != 'present' and any(value[key] for key in ('roots', 'sites', 'dispatch', 'references', 'chain')):
        raise ValueError('Absent or unanswered targets cannot submit candidate locations')
    return value


def admit(files, contract):
    """Admit an exact artifact set; inference explanation is a separate artifact.

    A contract explicitly registers its names and versions. C17 source bytes are
    handed unchanged to the C evaluator; JSON never passes a code-fence extractor.
    """
    if not isinstance(files, dict) or not isinstance(contract, dict) or set(files) != set(contract):
        raise ValueError('Submission artifact set differs from its contract')
    admitted = {}
    for name, version in contract.items():
        if name not in ('locations.json', 'types.json', 'abi.json', 'solution.c', 'solution.cpp') or type(version) is not int or version != 1:
            raise ValueError('Unregistered artifact name or version')
        raw = files[name]
        if not isinstance(raw, bytes) or not 1 <= len(raw) <= MAX_BYTES:
            raise ValueError('Artifact byte limit exceeded')
        if name == 'locations.json':
            value = locations(decode(raw))
        elif name in ('types.json', 'abi.json'):
            from capabilities.abi import artifacts as type_artifacts
            value = (type_artifacts.types if name == 'types.json' else type_artifacts.abi)(decode(raw))
        else:
            try:
                value = raw.decode('utf-8')
            except UnicodeError as error:
                raise ValueError('Native source is not valid UTF-8') from error
            if '\x00' in value:
                raise ValueError('Native source contains a NUL byte')
        admitted[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw), 'value': value}
    return admitted
