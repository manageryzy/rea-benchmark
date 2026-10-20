"""Public instructions only: no reference implementation or test answers."""
import json
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1]
INTERFACES = {
    'cpp': ('cpp', 'std::int64_t reconstructed(std::span<const std::uint8_t> data);',
            'Use C++20. Include <cstdint> and <span>. Do not add main.'),
    'rust': ('rust', 'pub fn reconstructed(data: &[u8]) -> i64',
             'Use Rust edition 2021. Submit a function, with no main, crate attributes or external crates.'),
    'go': ('go', 'func reconstructed(data []byte) int64',
           'Use Go. Include package main in the same code block, with no main function. Standard-library imports are allowed.'),
}

def write_prompts():
    for language, (fence, signature, requirements) in INTERFACES.items():
        public = BENCH / 'public' / language
        manifest = json.loads((public / 'manifest.json').read_text())
        prompt = f'''Analyze target with REA's Ghidra provider. The original language is {language}.
The target function is {manifest['target_function']}. Recover its behavior from
the binary, pseudocode and assembly. Return a compilable reconstruction in the
original language, preserving validation order, integer widths, byte order,
loop bounds, wrapping arithmetic and signed return values.

Return a short behavioral explanation, evidence addresses, and exactly one
code block tagged {fence}, defining this function:
{signature}
{requirements}
The input is a byte span/slice of length 0 through 300. The grading driver owns
input/output and calls your function. No I/O, filesystem access, inline assembly,
FFI, unsafe code, external dependencies or subprocesses. Helper functions and
standard-library types are allowed inside the same code block.
You are graded on executable behavior, not source similarity. Do not run the
target or use another model. Do not seek source, hidden tests or prior answers.
Use only the supplied REA tools. Open target with provider_id ghidra and close
it before finishing. There is a limit of 20 tool calls. Submit one final
reconstruction; no compile or grader feedback is available. If evidence is
incomplete, state the uncertainty.
'''
        (public / 'prompt.txt').write_text(prompt)
        print(f'{language}: prepared original-language reconstruction prompt')

if __name__ == '__main__':
    write_prompts()
