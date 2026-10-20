"""Build realistic optimized command registries, strip copies, and freeze inputs."""

import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]
ROOT = BENCH.parent
sys.path.insert(0, str(BENCH / "scripts"))
from grade import CPP_DRIVER, RUST_DRIVER, GO_DRIVER
from oracle import vectors

CPP_DRIVER = CPP_DRIVER.replace('printf("%lld\\n",(long long)reconstructed(std::span<const std::uint8_t>(bytes,count)));',
                                'std::cout << reconstructed(std::span<const std::uint8_t>(bytes,count)) << "\\n";')
CPP_DRIVER = CPP_DRIVER.replace('#include <span>', '#include <span>\n#include <iostream>')
CPP_DRIVER = CPP_DRIVER.replace("char line[2048]; uint8_t bytes[1024];", "char line[16384]; uint8_t bytes[8192];")


def run(command, **kwargs):
    return subprocess.run(command, text=True, capture_output=True, check=True, **kwargs)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry_points(binary, language):
    symbols = run(["nm", "-n", "-C", str(binary)]).stdout
    patterns = {
        "cpp": ["word_counts(", "ledger(", "rank_records(", "schedule(", "abi_record(", "apply_policy("],
        "rust": ["::word_counts", "::ledger", "::rank_records", "::schedule", "::abi_record", "::apply_policy"],
        "go": ["main.wordCounts", "main.ledger", "main.rankRecords", "main.schedule", "main.abiRecord", "main.applyPolicy"],
    }[language]
    result = {}
    for index, pattern in enumerate(patterns):
        matches = [line for line in symbols.splitlines() if pattern in line.replace('[abi:cxx11]', '') and
                   re.match(r"^[0-9a-f]+ [tT] ", line) and "[clone " not in line and ".func" not in line]
        if len(matches) != 1:
            raise RuntimeError(f"Ambiguous P{index} entry: {matches}")
        result[f"P{index}"] = "0x" + matches[0].split()[0].lstrip("0")
    dispatch = "main.reconstructed" if language == "go" else "reconstructed"
    matches = [line for line in symbols.splitlines() if dispatch in line and
               re.match(r"^[0-9a-f]+ [tT] ", line) and "[clone " not in line]
    if not matches and language == "rust":
        matches = [line for line in symbols.splitlines() if line.endswith(' driver::main')]
    assert len(matches) == 1, matches
    result["dispatch"] = "0x" + matches[0].split()[0].lstrip("0")
    return result, symbols


def prompt(language, entries):
    signature = {
        "cpp": "std::string reconstructed(std::span<const std::uint8_t> data);",
        "rust": "pub fn reconstructed(data: &[u8]) -> String",
        "go": "func reconstructed(data []byte) string",
    }[language]
    requirements = {
        "cpp": "Use C++20, include needed standard headers; no main function.",
        "rust": "Use Rust edition 2021; no main, external crates, unsafe code, or crate attributes.",
        "go": "Use Go with package main and needed standard imports; no main function or unsafe/cgo.",
    }[language]
    return f"""Recover this optimized, stripped Linux x86-64 ELF in its original language: {language}.
It contains six independent command handlers and a dispatcher. Reconstruct all
six handler behaviors from the binary. Produce human-readable, well-formatted,
idiomatic code with descriptive names, small helpers and useful comments.
Analyze the application functions and their callees, not the entire runtime.

Address hints locate code only; they do not specify its behavior:
{json.dumps(entries, indent=2)}
Point bodies: P0 arbitrary bytes; P1 repeated 4-byte records (u8,u8,i16LE);
P2 repeated 3-byte records (u8,i16LE); P3 u8 followed by pairs of u8;
P4 u8 followed by eight i16LE; P5 u8 followed by i16LE values.
The first request byte selects P0..P5. Empty/unknown requests also matter.
All requests have 0..512 bytes and each invocation begins with fresh state.
Return exactly the same printable result string as the target dispatcher.
No semantic formulas, error answers, original sources or tests are supplied.

Public submission signature:
{signature}
{requirements}
The grading driver owns stdin/stdout and calls reconstructed. No filesystem,
network, subprocesses, inline assembly or FFI. Helpers, types and standard
containers are allowed. Equivalent native implementations are accepted.
For P4 also explain observed argument passing and aggregate return conventions
with evidence addresses; separate observations from inference. The candidate
is tested for behavior, not binary-link compatibility with those ABI boundaries.

Use only supplied REA tools, provider ghidra, and open path target. No source,
hidden tests, prior answers, target execution, compiler feedback or other models.
Budget: 60 tool calls and 30 minutes. Close the binary before finishing.
Submit a short evidence-based explanation and exactly ONE {language} code block
containing the complete reconstruction. Code can precede the final close call.
There is one attempt with no grading feedback or repairs. State uncertainty.
"""


def main():
    private = SUITE / "private"
    private.mkdir(exist_ok=True)
    cases = vectors()
    vector_path = private / "vectors.json"
    vector_path.write_text(json.dumps(cases, indent=2) + "\n")
    payload = "".join(case["hex"] + "\n" for case in cases)
    expected = [case["expected"] for case in cases]
    results = []
    for language in ("cpp", "rust", "go"):
        sources = SUITE / "authoring" / language
        build = private / "build" / language
        build.mkdir(parents=True, exist_ok=True)
        binary = build / "target-with-symbols"
        environment = os.environ.copy()
        if language == "cpp":
            (build / "candidate.cpp").write_text((sources / "case.cpp").read_text())
            (build / "abi.hpp").write_text((sources / "abi.hpp").read_text())
            (build / "driver.cpp").write_text(CPP_DRIVER)
            commands = [["g++", "-std=c++20", "-O2", "-g0", "-no-pie", "-Wall", "-Wextra",
                         str(build / "driver.cpp"), str(sources / "abi.cpp"), "-o", str(binary)]]
            compiler = run(["g++", "--version"]).stdout.splitlines()[0]
        elif language == "rust":
            (build / "candidate.rs").write_text((sources / "case.rs").read_text())
            (build / "driver.rs").write_text(RUST_DRIVER)
            library = build / "libabi_bridge.rlib"
            common = ["-C", "opt-level=2", "-C", "debuginfo=0", "-C", "relocation-model=static"]
            commands = [["rustc", "--edition=2021", "--crate-name", "abi_bridge", "--crate-type=rlib",
                         *common, str(sources / "abi_bridge.rs"), "-o", str(library)],
                        ["rustc", "--edition=2021", *common, "-C", "link-arg=-no-pie",
                         "--extern", f"abi_bridge={library}", str(build / "driver.rs"), "-o", str(binary)]]
            compiler = run(["rustc", "--version"]).stdout.strip()
        else:
            (build / "candidate.go").write_text((sources / "case.go").read_text())
            (build / "driver.go").write_text(GO_DRIVER)
            environment.update(GO111MODULE="off", GOPROXY="off", CGO_ENABLED="0",
                               GOCACHE=str(ROOT / ".cache/complex-go"))
            commands = [["go", "build", "-trimpath", "-ldflags=-w", "-o", str(binary),
                         str(build / "driver.go"), str(build / "candidate.go")]]
            compiler = run(["go", "version"]).stdout.strip()
        logs = []
        for command in commands:
            completed = run(command, env=environment)
            logs.append(completed.stdout + completed.stderr)
        (build / "compile.log").write_text("\n".join(logs))
        entries, symbols = entry_points(binary, language)
        (build / "symbols.txt").write_text(symbols)
        public = SUITE / "public" / language / "optimized-stripped"
        public.mkdir(parents=True, exist_ok=True)
        target = public / "target"
        run(["cp", str(binary), str(target)])
        run(["strip", "--strip-all", str(target)])
        sections = run(["readelf", "-SW", str(target)]).stdout
        assert ".symtab" not in sections and ".debug_info" not in sections
        text_hashes = []
        for index, artifact in enumerate([binary, target]):
            text = build / f"text-{index}.bin"
            run(["objcopy", "--dump-section", f".text={text}", str(artifact)])
            text_hashes.append(sha(text))
            actual = run([str(artifact)], input=payload, timeout=30).stdout.splitlines()
            if actual != expected:
                mismatch = next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b), None)
                raise RuntimeError(f"{language}: oracle mismatch at {mismatch}: {actual[mismatch] if mismatch is not None else len(actual)}")
        assert text_hashes[0] == text_hashes[1]
        manifest = {
            "case_id": f"multipoint-container-abi-v1-{language}", "language": language,
            "architecture": "x86_64", "format": "ELF", "profile": "optimized-stripped",
            "binary_sha256": sha(target), "entry_points": entries,
            "compiler": compiler, "optimization": "normal optimized build; inlining enabled",
            "debug_info": False, "regular_symbols_retained": False,
            "runtime_metadata_retained": language == "go", "original_tests_passed": len(cases),
        }
        (public / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        (public / "prompt.txt").write_text(prompt(language, entries))
        (build / "build-manifest.json").write_text(json.dumps({
            **manifest, "commands": commands, "postprocess": ["strip", "--strip-all"],
            "source_hashes": {str(path.relative_to(SUITE)): sha(path) for path in sources.iterdir()},
            "vectors_sha256": sha(vector_path), "text_sha256": text_hashes[0],
            "optimized_symbolized_binary_sha256": sha(binary),
            "regular_symbols_removed": True, "text_unchanged_after_strip": True,
            "sections": sections,
        }, indent=2) + "\n")
        results.append(manifest)
        print(f"{language}: optimized + fully stripped regular symbols; {len(cases)} tests pass in BOTH copies", flush=True)
    (private / "build-validation.json").write_text(json.dumps({
        "passed": True, "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "vectors": len(cases), "vectors_sha256": sha(vector_path), "cases": results,
    }, indent=2) + "\n")


if __name__ == "__main__":
    main()
