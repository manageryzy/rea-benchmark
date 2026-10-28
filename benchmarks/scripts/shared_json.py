"""Read-only, JSON-compatible cache snapshots with explicit mutable copies.

Normal mapping/list mutation is refused recursively. Keep dict/list interfaces
and JSON encoding byte-compatible with validated manifests. Explicit deepcopy
still produces an independent mutable tree for image composition callers.
These are application value objects, not an isolation boundary for Python code.
"""
import copy


def _readonly(*args, **kwargs):
    raise TypeError('Shared cache JSON is read-only; use mutable_copy() before editing')


class ReadOnlyDict(dict):
    __slots__ = ()
    __init__ = __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _readonly

    def __copy__(self):
        return dict(self)

    def __deepcopy__(self, memo):
        return _mutable_copy(self, memo)


class ReadOnlyList(list):
    __slots__ = ()
    __init__ = __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _readonly

    def __copy__(self):
        return list(self)

    def __deepcopy__(self, memo):
        return _mutable_copy(self, memo)


def freeze(value):
    """Snapshot once; retain no mutable aliases to the source object graph."""
    memo, active = {}, set()

    def visit(item):
        if type(item) in (str, int, float, bool, type(None)):
            return item
        if type(item) in (ReadOnlyDict, ReadOnlyList):
            return item
        identity = id(item)
        if identity in active:
            raise ValueError('Cyclic values are not JSON')
        if identity in memo:
            return memo[identity]
        active.add(identity)
        try:
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise TypeError('Shared JSON object keys must be strings')
                result = dict.__new__(ReadOnlyDict)
                dict.__init__(result, ((key, visit(child)) for key, child in item.items()))
            elif type(item) is list:
                result = list.__new__(ReadOnlyList)
                list.__init__(result, (visit(child) for child in item))
            else:
                raise TypeError('Unsupported shared JSON value: ' + type(item).__name__)
            memo[identity] = result
            return result
        finally:
            active.remove(identity)
    return visit(value)


def _mutable_copy(value, memo):
    identity = id(value)
    if identity in memo:
        return memo[identity]
    if type(value) is ReadOnlyDict:
        result = {}
        memo[identity] = result
        result.update((key, copy.deepcopy(child, memo)) for key, child in value.items())
        return result
    if type(value) is ReadOnlyList:
        result = []
        memo[identity] = result
        result.extend(copy.deepcopy(child, memo) for child in value)
        return result
    return copy.deepcopy(value, memo)


def mutable_copy(value):
    """Opt into a separately editable copy, never mutate a shared cache entry."""
    return _mutable_copy(value, {})
