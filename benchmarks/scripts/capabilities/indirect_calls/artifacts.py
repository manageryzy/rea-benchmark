"""Bounded call relationships under a complete supplied root layout and ABI."""
from grading.artifacts import object_fields, integer, digest, sequence, unique


def admit(value):
    object_fields(value, ('schema_version', 'binary_sha256', 'operations'))
    integer(value['schema_version'], 1, 1)
    digest(value['binary_sha256'])
    operations = sequence(value['operations'], 2)
    identifiers, receivers, callbacks, contexts = [], [], [], []
    for operation in operations:
        object_fields(operation, ('operation', 'receiver_slot', 'callback_slot', 'context_slot',
                                  'virtual_slot', 'this_adjustment'))
        integer(operation['operation'], 0, 1)
        for name in ('receiver_slot', 'callback_slot', 'context_slot'):
            integer(operation[name], 0, 1)
        integer(operation['virtual_slot'], 0, 3)
        integer(operation['this_adjustment'], -128, 128)
        if operation['this_adjustment'] % 8:
            raise ValueError('Unsupported receiver adjustment granularity')
        identifiers.append(operation['operation'])
        receivers.append(operation['receiver_slot'])
        callbacks.append(operation['callback_slot'])
        contexts.append(operation['context_slot'])
    for values in (identifiers, receivers, callbacks, contexts):
        unique(values)
    if set(identifiers) != {0, 1}:
        raise ValueError('Both supplied operation identities are required')
    return value
