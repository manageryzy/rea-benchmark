"""Explicit evidence access for independently registered type-recovery controls.

Recovery keeps its existing input bytes and cache namespace. Premises-only
controls retain the original public contract but physically withhold target
bytes and expose an empty analysis-tool catalog. They are separate observations.
"""
import hashlib
import json

MODES = ('binary', 'premises_only')
TYPE_KINDS = frozenset(('abi_probe', 'pointer_probe', 'variant_object',
                       'indirect_call', 'native_abi'))
NOTICE = (b'\n\nCONTROL CONDITION: PREMISES ONLY. Target bytes and all analysis tools '
          b'are deliberately withheld. Do not open or close a binary, claim binary '
          b'inspection, or seek previous answers. Use only the supplied public '
          b'premises to produce the same requested artifact, retaining its original '
          b'binary_sha256 binding. This control is separate from binary recovery.\n')


def mode(value='binary'):
    if not isinstance(value, str) or value not in MODES:
        raise ValueError('Unsupported capability evidence access')
    return value


def validate_selection(selection, declarations):
    access = mode(selection.get('evidence_access', 'binary'))
    if access == 'binary':
        return access
    if (selection.get('suites') != ['capabilities-v1']
            or selection.get('experiments') != ['capabilities-v1']
            or selection.get('capabilities') is not True
            or not isinstance(selection.get('cases'), list)
            or not selection['cases']):
        raise ValueError('Premises-only controls require explicit capability task selection')
    eligible = {row['task_id'] for row in declarations if row['profile']['kind'] in TYPE_KINDS}
    names = {name.removeprefix('capabilities-v1/') for name in selection['cases']}
    if names - eligible:
        raise ValueError('Premises-only controls support only declared type/ABI recovery tasks')
    return access


def project(files, access):
    """Derive model inputs, never read private specifications or prior answers."""
    if mode(access) == 'binary':
        return files
    if set(files) != {'target', 'prompt.txt', 'manifest.json', 'tools.json'}:
        raise ValueError('Unexpected public input in premises-only control')
    manifest = json.loads(files['manifest.json'])
    if hashlib.sha256(files['target']).hexdigest() != manifest['binary_sha256']:
        raise ValueError('Original target differs from the preserved public contract')
    catalog = json.loads(files['tools.json'])
    if not isinstance(catalog.get('tools'), list):
        raise ValueError('Invalid original analysis-tool catalog')
    return {**files, 'target': b'', 'prompt.txt': files['prompt.txt'] + NOTICE,
            'tools.json': json.dumps({**catalog, 'tools': []}, sort_keys=True,
                                     separators=(',', ':'), ensure_ascii=False).encode()}


def analysis_condition(access, recovery_condition):
    return 'premises-only-v1' if mode(access) == 'premises_only' else recovery_condition


def validate_inputs(files, access, *, binary_sha256, allowed_tools):
    """Assert actual materialized bytes and sealed tool permission, not a label."""
    if mode(access) == 'binary':
        return
    if set(files) != {'target', 'prompt.txt', 'manifest.json', 'tools.json'}:
        raise ValueError('Unexpected premises-only runtime artifact')
    if (files['target'] != b'' or json.loads(files['tools.json']).get('tools') != []
            or allowed_tools != [] or not files['prompt.txt'].endswith(NOTICE)
            or json.loads(files['manifest.json'])['binary_sha256'] != binary_sha256):
        raise ValueError('Premises-only materialization retained forbidden evidence or lost its binding')


def validate_observation(task, files=None):
    """Do not classify a withheld-target observation by an editable label alone."""
    access = mode(task.get('evidence_access', 'binary'))
    identity = task.get('inference_identity', {})
    if access == 'binary':
        if 'binary' in identity and identity['binary'] != task['binary_sha256']:
            raise ValueError('Recovery observation did not receive its bound target bytes')
        return access
    if files is None or 'allowed_tool_names' not in task.get('request_contract', {}):
        raise ValueError('Premises-only observation lacks materialized input or tool-permission evidence')
    validate_inputs(files, access, binary_sha256=task['binary_sha256'],
                    allowed_tools=task['request_contract']['allowed_tool_names'])
    for name, key in (('target', 'binary'), ('prompt.txt', 'prompt'),
                      ('tools.json', 'tools'), ('manifest.json', 'manifest')):
        if hashlib.sha256(files[name]).hexdigest() != identity.get(key):
            raise ValueError('Premises-only input differs from its committed inference identity')
    if hashlib.sha256(files['manifest.json']).hexdigest() != task['public_files_sha256']['manifest.json']:
        raise ValueError('Premises-only control changed its original public premises')
    return access
