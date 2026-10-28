"""Public serialization clarification, independent of measured type facts."""
POLICY = 'record-role-string-v1'
SUFFIX = '.role-string-v1'
NOTICE = (b'\n\nPUBLIC SUBMISSION FORMAT: In types.json, every record fields entry has '
          b'role as ONE ASCII C-style identifier STRING of 1..64 characters, not a path array. '
          b'For example, "role":"member" is valid syntax; "role":["member"] is not. '
          b'given_facts.path is a path array and must not be copied into role. '
          b'Nested semantic paths are formed from successive record-role strings '
          b'and array indices. root, type and element are type-ID strings; signed '
          b'is a JSON boolean. This clarification supplies no widths, signedness, '
          b'offsets or layouts.\n')


def enabled(runtime):
    value = runtime.get('explicit_type_roles', False)
    if type(value) is not bool:
        raise ValueError('explicit_type_roles must be boolean')
    return value


def policy(case, runtime):
    return POLICY if enabled(runtime) and case.get('kind') == 'abi_probe' and case.get('artifact_schema') == {'types.json': 1} else None


def notice(value):
    if value not in (None, POLICY):
        raise ValueError('Unsupported public artifact-format policy')
    return NOTICE if value else b''


def condition(base, value):
    notice(value)
    return base + SUFFIX if value else base
