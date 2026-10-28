"""Private localization truth and hint-free prompts from the same frozen build.

Semantic roles use explicitly reviewed instructions from a matching binary, not
assembler labels: the optimizer can move unrelated instructions under a label.
Byte-bound reviews distinguish required operations from permitted local context.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

import action_cache
from .build import ROOT, SUITE
from .elf import stripped_matches
from .public import manifest

INSTRUCTION = re.compile(r'^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )*[0-9a-f]{2})\s+(.+)$')


def instructions(text, elf):
    result = {}
    for line in text.splitlines():
        match = INSTRUCTION.match(line)
        if not match:
            continue
        address, raw, assembly = int(match[1], 16) - elf.base, bytes.fromhex(match[2]), match[3]
        if not 1 <= len(raw) <= 15 or elf.bytes_at(address, len(raw)) != raw:
            raise ValueError('Disassembly does not match final target bytes')
        result[address] = {'size': len(raw), 'assembly': assembly}
    if not result:
        raise ValueError('No validated target instructions')
    return result


def _role_contract(family):
    descriptions = family.get('role_descriptions')
    if (not isinstance(descriptions, dict) or set(descriptions) != set(family['roles'])
            or any(not isinstance(text, str) or not text.strip() or len(text) > 2000
                   for text in descriptions.values())):
        raise ValueError('Public semantic role descriptions differ from private roles')
    return descriptions


def private_truth(family_id, build, *, absent=False):
    private, public = stripped_matches(bytes.fromhex(build['private_hex']), bytes.fromhex(build['public_hex']))
    catalog = json.loads((SUITE / 'config/families.json').read_bytes())
    families = catalog['families']
    family = next(row for row in families if row['id'] == family_id)
    _role_contract(family)
    symbols = private.symbols()
    decoded = instructions(build['disassembly'], public)
    rows = {'schema_version': 1, 'validated': True, 'present': not absent,
            'binary_sha256': hashlib.sha256(public.raw).hexdigest(), 'segments': public.segments(),
            'instruction_starts': sorted(decoded),
            'instruction_ends': sorted(address + item['size'] for address, item in decoded.items()),
            'roots': [], 'roles': {}, 'role_context': {},
            'semantic_policy': 'required-sites-context-v1', 'dispatch': [],
            'references': [], 'chain': {'nodes': [], 'edges': []}}
    if absent:
        if any(name in symbols for name in family['chain'] if name != family['root']):
            raise ValueError('Absent-target control retained requested helper behavior')
        return rows
    root = symbols[family['root']]['rva']
    if root not in decoded:
        raise ValueError('Business root is not an instruction boundary')
    rows['roots'] = [root]
    review = catalog.get('semantic_review', {})
    matches = [item for item in review.get('builds', [])
               if item['family_id'] == family_id and item['binary_sha256'] == rows['binary_sha256']]
    if review.get('revision') != rows['semantic_policy'] or len(matches) != 1:
        raise ValueError('Semantic instruction review missing for this exact binary')
    reviewed = matches[0]
    if set(reviewed['roles']) != set(family['roles']):
        raise ValueError('Reviewed semantic roles differ from the public contract')
    for role, parts in reviewed['roles'].items():
        if set(parts) != {'required', 'context'} or not parts['required']:
            raise ValueError('Invalid semantic instruction review')
        seen = set()
        for key, destination in [('required', rows['roles']), ('context', rows['role_context'])]:
            ranges = []
            for item in parts[key]:
                address = item['rva']
                if (type(address) is not int or address in seen or address not in decoded
                        or bytes.fromhex(item['bytes']) != public.bytes_at(address, decoded[address]['size'])):
                    raise ValueError('Reviewed semantic instruction bytes differ from target')
                seen.add(address)
                ranges.append({'start': address, 'end': address + decoded[address]['size']})
            destination[role] = ranges
    functions = {name: symbols[name] for name in family['chain']}
    nodes = [row['rva'] for row in functions.values()]
    rows['chain']['nodes'] = nodes
    for name, function in functions.items():
        body = {address: item for address, item in decoded.items()
                if function['rva'] <= address < function['rva'] + function['size']}
        for address, item in body.items():
            match = re.match(r'call\s+([0-9a-f]+)\s', item['assembly'])
            if match:
                target = int(match[1], 16) - public.base
                if target in nodes:
                    rows['chain']['edges'].append([function['rva'], target])
    if family_id == 'dispatch_registry':
        table = next(row for name, row in symbols.items() if name.endswith('policy_table'))
        expected = [symbols[name]['address'] for name in ('add_policy', 'xor_policy')]
        import struct
        actual = list(struct.unpack('<QQ', private.bytes_at(table['rva'], 16)))
        if actual != expected:
            raise ValueError('Private dispatch table differs from actual target pointers')
        call = _indirect_site(decoded, rows['roles']['policy_call'][0]['start'], functions['family_root'])
        for condition, name in [('low_bit_zero', 'add_policy'), ('low_bit_one', 'xor_policy')]:
            target = symbols[name]['rva']
            rows['dispatch'].append({'condition': condition, 'site': call, 'target': target})
            rows['chain']['edges'].append([root, target])
    if family_id == 'callback_policy':
        call = _indirect_site(decoded, rows['roles']['callback_invoke'][0]['start'], functions['apply_callback'])
        rows['dispatch'].append({'condition': 'each_byte', 'site': call, 'target': symbols['transform_item']['rva']})
        rows['chain']['edges'].append([symbols['apply_callback']['rva'], symbols['transform_item']['rva']])
    # Only statically retained, defined objects are scored here. Variable input
    # buffers/stack objects need a different declared access proof, not guesses.
    objects = {row['rva']: row for name, row in symbols.items()
               if row['type'] == 1 and row['size'] and any(token in name for token in ('codec_salt', 'policy_table'))}
    for address, item in decoded.items():
        assembly = item['assembly']
        if not any(row['rva'] <= address < row['rva'] + row['size'] for row in functions.values()):
            continue
        match = re.search(r'#\s*([0-9a-f]+)\s', assembly)
        absolute = re.search(r'\b0x([0-9a-f]+)\(', assembly) if not match else None
        target = int((match or absolute)[1], 16) - public.base if match or absolute else None
        if target in objects:
            # Reviewed family references are mov loads, or lea address creation.
            # AT&T operand order distinguishes load from store; reject a change
            # rather than silently reinterpret it as the expected access kind.
            operation = assembly.split(None, 1)[0]
            operands = assembly.split('#', 1)[0].split(None, 1)[1]
            if operation.startswith('lea'):
                access = 'address'
            elif operation.startswith('mov') and operands.rsplit(',', 1)[-1].strip().startswith('%'):
                access = 'read'
            else:
                raise ValueError('Unexpected target data-access instruction')
            rows['references'].append({'site': address, 'object': target, 'length': objects[target]['size'], 'access': access})
    rows['chain']['edges'] = [list(edge) for edge in sorted(set(tuple(edge) for edge in rows['chain']['edges']))]
    if any(not any(node in edge for edge in rows['chain']['edges']) for node in nodes):
        raise ValueError('Private chain has an unvalidated disconnected node')
    return rows


def _indirect_site(decoded, start, function):
    candidates = [address for address, item in sorted(decoded.items())
                  if start <= address < function['rva'] + function['size'] and re.match(r'call\s+\*', item['assembly'])]
    if not candidates:
        raise ValueError('Expected indirect dispatch did not survive compilation')
    return candidates[0]


def metadata(family_id, build, truth):
    return {'family_id': family_id, 'build_id': build['build_id'], 'source_language': 'cpp',
            'binary_sha256': truth['binary_sha256'], 'architecture': 'x86_64'}


def prompt(family_id, capability):
    family = next(row for row in json.loads((SUITE / 'config/families.json').read_bytes())['families'] if row['id'] == family_id)
    questions = {
        'L1': 'Identify the primary top-1 business-function root. A similar distractor exists. The requested behavior may be absent.',
        'L2': 'Identify narrow instruction ranges for the semantic roles defined below.',
        'L3': 'Identify conditional indirect-call sites and targets. Conditions are low_bit_zero/low_bit_one for byte-policy selection, or each_byte for callback iteration, where present.',
        'L4': 'Identify references to the retained fixed policy table or salt object, where present. Describe the full referenced object range and read/write/address role.',
        'L5': 'Identify the business root and its bounded directional call chain. Include indirect edges when required by the described behavior.'}
    if capability not in questions:
        raise ValueError('Unregistered localization prompt')
    roles = '\n'.join(role + ': ' + text for role, text in _role_contract(family).items())
    return ('Analyze only target using the supplied static REA/Ghidra tools.\n'
            'Requested behavior: ' + family['behavior'] + '\n' + questions[capability] + '\n'
            'The business root is the scalar function taking a byte pointer and byte count and returning an unsigned 64-bit value for the complete requested operation. '
            'Exclude its individual decoding, aggregation, iteration or state-update helpers, and exclude the buffer serializer and CLI frontend.\n'
            'Semantic instruction roles (all required operations must be covered; optional context is accepted but cannot replace an operation):\n' + roles + '\n'
            'Dispatch condition IDs: low_bit_zero and low_bit_one for byte-policy selection; each_byte for callback iteration. Leave dispatch empty for a family without an indirect call.\n'
            'References describe the full retained fixed salt or policy-table object and read/write/address access; leave references empty when neither exists.\n'
            'No source, prior task answers, target execution or compiler feedback is available.\n'
            'Submit locations.json schema version 1. RVAs are offsets from the lowest PT_LOAD page, for both PIE and ET_EXEC.\n'
            'Use status present, absent or unanswered. Supply at most 3 roots (only the first is scored), 16 sites (each at most 64 bytes, 256 bytes total),\n'
            '24 dispatch edges, 24 references, 16 chain nodes and 24 chain edges. Unknown facts can be left empty; do not list the whole program.\n'
            'All fields are required: schema_version, binary_sha256, status, roots, sites, dispatch, references, chain (null when omitted).\n'
            'Site fields: role/start/end. Dispatch fields: condition/site/target. Reference fields: site/object/length/access.\n'
            'Chain fields: root/nodes/edges; edges are [source,target]. Close the binary when finished.\n'
            'JSON type contract (locations.json v1): use one JSON object, with no unknown or duplicate keys.\n'
            "schema_version is the JSON integer 1; binary_sha256 is the target's 64 lowercase hexadecimal digits in a JSON string.\n"
            'status is the JSON string "present", "absent" or "unanswered".\n'
            'ALL RVAs, schema_version, and reference lengths are JSON integer NUMBERS, never strings, floats, booleans, or objects. JSON numbers use decimal notation: an observed RVA 0x1234 is submitted as 4660, not "0x1234". This is only a notation example, not a target address.\n'
            'roots is an array of at most 3 integer RVAs, e.g. [4660], NOT [{"rva":4660}], NOT [{"start":4660,"end":4670}]. Only roots[0] is scored. All roots and chain nodes must be unique.\n'
            'sites is an array of objects with exactly role (one identifier string), start (integer RVA), end (integer RVA). Ranges are half-open [start,end); 1 <= end-start <= 64; at most 16 sites and 256 bytes total.\n'
            'dispatch is an array of at most 24 objects with exactly condition (one condition identifier string), site (one integer callsite RVA), target (one integer target RVA). site is NOT a range object.\n'
            'references is an array of at most 24 objects with exactly site (integer instruction RVA), object (integer object RVA), length (integer 1..4096), access (string "read", "write", "read_write" or "address").\n'
            'chain is null or an object with exactly root (integer RVA), nodes (at most 16 unique integer RVAs), edges (at most 24 unique [integer source RVA, integer target RVA] arrays). root must occur in nodes; both endpoints of every edge must occur in nodes.\n'
            'Every RVA is an unsigned integer <= 18446744073709551615. Identifier strings match [A-Za-z_][A-Za-z0-9_]{0,63}; all array records must be unique. Do not add names, notes, signatures or address wrappers to these records.\n'
            'For status absent or unanswered, roots/sites/dispatch/references are [] and chain is null. For status present, unknown facts may remain empty; chain may be null.\n'
            'Use RVAs relative to the lowest PT_LOAD page, not absolute virtual addresses, file offsets or automatically rebased guesses. Emit exactly one fenced block named locations.json; explanatory prose is not part of the JSON artifact.\n'
            )


def action(submitted_files, truth):
    """Independent grade action: exact artifacts + truth + only relevant code."""
    sources = ('benchmarks/scripts/grading/__init__.py', 'benchmarks/scripts/grading/artifacts.py',
               'benchmarks/scripts/grading/localization.py')
    inputs = {name: (ROOT / name).read_bytes() for name in sources}
    inputs.update({'artifact/' + name: raw for name, raw in submitted_files.items()})
    inputs['private/truth.json'] = action_cache.canonical(truth)
    # This key is for offline preparation controls. A released capability grade
    # still needs a closed artifact-worker runtime; a host interpreter digest
    # alone is deliberately not labelled runtime attestation.
    runtime = {'scope': 'host_python_preparation_only', 'python': sys.version,
               'implementation': sys.implementation.name,
               'executable_sha256': hashlib.sha256(Path(sys.executable).resolve().read_bytes()).hexdigest()}
    return action_cache.Action('localization_grade_v1', inputs, arguments=['locations-v1'], platform=runtime)


def answer(truth):
    """Private positive control only; never included in a public task package."""
    return {'schema_version': 1, 'binary_sha256': truth['binary_sha256'], 'status': 'present' if truth['present'] else 'absent',
            'roots': truth['roots'], 'sites': [{'role': role, **region} for role, ranges in truth['roles'].items() for region in ranges],
            'dispatch': truth['dispatch'], 'references': truth['references'],
            'chain': {'root': truth['roots'][0], **truth['chain']} if truth['present'] else None}
