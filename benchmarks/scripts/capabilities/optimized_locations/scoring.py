"""Diagnostic matching of submitted realizations against reviewed code bytes.

Compiler-history categories are private analysis factors, never labels a model
must guess from indistinguishable stripped instruction sequences. One submitted
realization can match at most one reviewed realization, and vice versa.
"""
from fractions import Fraction

from capabilities import elf, locations
from . import artifact, review


def truth(target, mode):
    verified = review.inspect(target, mode)
    image = elf.ELF(bytes.fromhex(target['private_hex']))
    instructions = locations.instructions(target['disassembly'], image)
    realizations = []
    for kind, instances in verified['groups'].items():
        for instance in instances:
            parts = instance['ranges']
            def required_for(regions):
                return {address for address, row in instructions.items()
                        if any(p['start'] <= address < p['end'] for p in regions)
                        and image.bytes_at(address, row['size']) != b'\xc3'}
            required = required_for(parts)
            alternatives = [{'ranges': [{'start': p['start'], 'end': p['end']} for p in regions],
                             'required': sorted(required_for(regions))}
                            for regions in [parts] + instance.get('alternative_ranges', [])]
            all_parts = parts + instance.get('entry_paths', []) + [
                region for regions in instance.get('alternative_ranges', []) for region in regions]
            allowed = {address for address in instructions
                       if any(p['start'] <= address < p['end'] for p in all_parts)}
            if not required or any(not row['required'] or not set(row['required']) <= allowed for row in alternatives):
                raise ValueError('Empty or inconsistent optimized realization')
            realizations.append({'kind': kind, 'ranges': [{'start': p['start'], 'end': p['end']} for p in parts],
                                 'required': sorted(required), 'allowed': sorted(allowed), 'alternatives': alternatives})
    if not 1 <= len(realizations) <= 12:
        raise ValueError('Optimized diagnostic matching exceeds its declared size')
    return {'schema_version': 1, 'binary_sha256': verified['binary_sha256'],
            'review': verified['review'], 'independent_review': False, 'release_accepted': False,
            'realizations': realizations, 'instruction_starts': sorted(instructions),
            'instruction_ends': sorted(address + row['size'] for address, row in instructions.items())}


def answer(private):
    return {'schema_version': 1, 'binary_sha256': private['binary_sha256'],
            'instances': [{'ranges': row['ranges']} for row in private['realizations']]}


def prompt():
    return '''Find the complete machine-code realizations of this operation in the stripped Linux AMD64 binary:
uint64 result = rotl64(input XOR 0x93d50b7a6241ef8c, 7) * 33 + 109, with uint64 wraparound.
Return instances.json with schema_version 1, binary_sha256 and instances.
Each instance contains only ranges: a list of {start, end} half-open RVAs from the lowest load page.
Use instruction boundaries. Include all required arithmetic and control transfers for a realization;
returns and direct entry adapters are optional context. Keep separate realizations separate.
For a split hot/cold operation, include both partitions and its arithmetic helper in one instance.
A shared specialized implementation reached by multiple entry adapters is one realization.
For a shared receiver implementation, any complete reviewed adjusting path with that implementation is sufficient.
For a receiver adapter, include its adjustment and implementation together. Its supplied premise is:
the secondary-base pointer is owner+16; owner+8 stores the stated XOR constant; input is the second SysV argument.
Other scalar realizations take input in the first SysV argument. A receiver wrapper calling the adapter is context.
The receiver is the one initialized by this program for the stated operation; find its dispatch from the binary.
Do not include near-miss arithmetic. Do not guess original symbol names or compiler transformation labels.
This condition is diagnostic preparation; source provenance categories are private reporting factors.
JSON type contract (instances.json v1): return one JSON object with exactly schema_version, binary_sha256 and instances; no unknown or duplicate keys.
schema_version is the JSON integer 1. binary_sha256 is the target's 64 lowercase hexadecimal digits in a JSON string.
instances is an array of at most 128 objects, each with exactly ranges. ranges is an array of 1..16 objects, each with exactly start and end.
start and end are JSON integer NUMBERS, never strings, floats, booleans or address objects. JSON numbers use decimal notation: an observed RVA 0x1234 is submitted as 4660, not "0x1234". This is only a notation example, not a target address.
Each range is half-open [start,end), with 0 <= start < end < 281474976710656 and end-start <= 4096. No overlapping or repeated ranges within an instance, and no duplicate instances.
Use RVAs relative to the lowest load page, not absolute virtual addresses or file offsets. Do not add role, kind, symbol names or compiler-history labels. Empty instances means no submitted realization.
Emit exactly one fenced block named instances.json. The exact UTF-8 JSON artifact must not exceed 131072 bytes.
'''


def grade(raw, private):
    submitted = artifact.admit(raw)
    if submitted['binary_sha256'] != private['binary_sha256']:
        raise ValueError('Optimized artifact targets another binary')
    if (private.get('review') != 'coordinating_exact_instruction_review_v1'
            or private.get('release_accepted') is not False):
        raise ValueError('Unreviewed optimized diagnostic truth')
    starts, ends = set(private['instruction_starts']), set(private['instruction_ends'])
    predictions = []
    for instance in submitted['instances']:
        predicted = set()
        for part in instance['ranges']:
            points = {address for address in starts if part['start'] <= address < part['end']}
            predicted.update(points)
            if not points:
                predicted.add(('unknown_range', part['start'], part['end']))
            if part['start'] not in starts:
                predicted.add(('partial_start', part['start']))
            if part['end'] not in ends:
                predicted.add(('partial_end', part['end']))
        predictions.append(predicted)
    expected = private['realizations']

    def pair(predicted, actual):
        allowed = set(actual['allowed'])
        variants = actual.get('alternatives', [{'required': actual['required']}])
        coverage = max(Fraction(len(predicted & set(row['required'])), len(row['required'])) for row in variants)
        # Normalize alternatives to this realization's fixed primary weight;
        # a longer accepted implementation cannot inflate the denominator.
        correct, accepted = coverage * len(actual['required']), len(predicted & allowed)
        precision = Fraction(accepted, len(predicted)) if predicted else Fraction(0)
        f1 = 2 * precision * coverage / (precision + coverage) if precision + coverage else Fraction(0)
        return correct, accepted, coverage, precision, f1

    pairs = [[pair(predicted, actual) for actual in expected] for predicted in predictions]
    # At most twelve truth realizations; dynamic programming finds the global
    # best one-to-one assignment, avoiding order-sensitive greedy matching.
    states = {0: (Fraction(0), ())}
    for prediction, row in enumerate(pairs):
        updated = dict(states)
        for mask, (weight, assignments) in states.items():
            for index, score in enumerate(row):
                if mask & (1 << index) or not score[4]:
                    continue
                key, proposal = mask | (1 << index), (weight + score[4], assignments + ((prediction, index),))
                if key not in updated or proposal[0] > updated[key][0]:
                    updated[key] = proposal
        states = updated
    _, assignments = max(states.values(), key=lambda row: row[0])
    assigned = {index: prediction for prediction, index in assignments}
    correct = accepted = full = 0
    diagnostics = {kind: [] for kind in artifact.KINDS if any(row['kind'] == kind for row in expected)}
    for index, actual in enumerate(expected):
        prediction = assigned.get(index)
        score = pairs[prediction][index] if prediction is not None else (0, 0, Fraction(0), Fraction(0), Fraction(0))
        correct += score[0]
        accepted += score[1]
        full += score[4] == 1
        diagnostics[actual['kind']].append(float(score[4]))
    count = sum(len(predicted) for predicted in predictions)
    total = sum(len(row['required']) for row in expected)
    coverage = float(correct / total)
    precision = accepted / count if count else 0.0
    f1 = 2 * precision * coverage / (precision + coverage) if precision + coverage else 0.0
    return {'schema_version': 1, 'capability_ids': ['L6'], 'diagnostic_only': True,
            'release_accepted': False, 'artifact_valid': True, 'expected_instances': len(expected),
            'submitted_instances': len(predictions), 'complete_instances': full,
            'precision': precision, 'coverage': coverage, 'f1': f1,
            'complete': full == len(expected) == len(predictions) and f1 == 1.0,
            'compiler_phenomenon_diagnostics': diagnostics}
