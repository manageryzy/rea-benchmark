"""Positive, wrong-output and wrong-language controls; all cached, no inference."""
import hashlib
import json
from pathlib import Path
from grade import grade

SUITE = Path(__file__).resolve().parents[1]


def main():
    validation = json.loads((SUITE / 'private/validation.json').read_text())
    code = (SUITE / 'authoring/containers.cpp').read_text()
    include = (SUITE / 'authoring/grammar14.inc').read_text()
    expanded = code.replace('#include "grammar14.inc"', include)
    # For strict C++11 the inactive C++14 tokens must not be lexed at all.
    start = code.index('#if __cplusplus >= 201402L')
    middle = code.index('#else', start)
    end = code.index('#endif', middle) + len('#endif')
    code11 = code[:start] + code[middle + len('#else'):code.index('#endif', middle)] + code[end:]
    controls = []
    for build in validation['builds']:
        standard = build['standard']
        for kind in ('positive', 'wrong-output', 'wrong-language'):
            candidate = code11 if standard == 'c++11' else expanded
            tag = 'cpp'
            if kind == 'wrong-output': candidate = '#include <string>\n#include <vector>\n#include <cstdint>\nstd::string reconstructed(const std::vector<std::uint8_t>&){return "ERR:POINT";}\n'
            if kind == 'wrong-language': tag, candidate = 'rust', 'pub fn reconstructed(_: &[u8])->String { String::new() }\n'
            answer = f'```{tag}\n{candidate}```\n'
            key = hashlib.sha256(answer.encode()).hexdigest()
            directory = SUITE / 'private/grader-controls' / key / build['id'] / kind
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'answer.md').write_text(answer)
            receipt = grade(directory, build['id'])
            if kind == 'positive': assert receipt['program_success'] and receipt['passed'] == 2842
            elif kind == 'wrong-output': assert receipt['compiled'] and receipt['passed'] == 4 and not receipt['program_success']
            else: assert not receipt['compiled'] and receipt['failure'] == 'expected_one_cpp_code_block'
            controls.append({'case_id': build['id'], 'kind': kind, 'passed': True, 'cache_hit': receipt['cache_hit'], 'receipt': str(directory / 'grade.json')})
            print(f"{build['id']} {kind}: PASS{' (cache)' if receipt['cache_hit'] else ''}", flush=True)
    (SUITE / 'private/grader-validation.json').write_text(json.dumps({'passed': True, 'controls': controls, 'model_calls': 0}, indent=2) + '\n')


if __name__ == '__main__':
    main()
