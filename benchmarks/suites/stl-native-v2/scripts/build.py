"""Build once per content fingerprint; re-use validated binaries on cache hits."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from oracle import vectors

SUITE = Path(__file__).resolve().parents[1]
ROOT = SUITE.parents[2]
POINT_NAMES = ["vector", "array", "deque", "list", "forward_list", "set", "multiset", "map", "multimap",
               "unordered_set", "unordered_multiset", "unordered_map", "unordered_multimap", "stack", "queue",
               "priority_queue", "vector_bool", "string", "bitset", "valarray", "grammar11", "grammar14"]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command, **kwargs):
    return subprocess.run(command, capture_output=True, text=True, check=True, timeout=120, **kwargs)


def main():
    compiler = os.environ.get("CXX", "/usr/bin/g++")
    version = run([compiler, "--version"]).stdout
    compiler_path = Path(shutil.which(compiler)).resolve()
    # Include compiler, standard-library headers/runtime and scripts in the key.
    headers = Path("/usr/include/c++/13")
    source_paths = [SUITE / name for name in ("authoring/containers.cpp", "authoring/driver.cpp", "authoring/grammar14.inc", "scripts/build.py", "scripts/oracle.py")]
    source_hashes = {str(p.relative_to(SUITE)): digest(p) for p in source_paths}
    architecture_headers = Path("/usr/include/x86_64-linux-gnu/c++/13")
    dependencies = {str(p): digest(p) for p in [compiler_path, *sorted(headers.rglob("*")), *sorted(architecture_headers.rglob("*"))] if p.is_file()}
    for library in ("libstdc++.so", "libgcc_s.so"):
        path = Path(run([compiler, f"-print-file-name={library}"]).stdout.strip()).resolve()
        if path.is_file(): dependencies[str(path)] = digest(path)
    fingerprint = hashlib.sha256(json.dumps({"sources": source_hashes, "compiler": version,
        "dependencies": dependencies, "profile": "O2/g0/strip-all/pedantic/Werror/two-dialects/two-string-ABIs"}, sort_keys=True).encode()).hexdigest()
    output = SUITE / "private" / "build-cache" / fingerprint
    receipt = output / "validation.json"
    if receipt.exists():
        prior = json.loads(receipt.read_text())
        if prior["passed"] and all((output / name).is_file() and digest(output / name) == sha for name, sha in prior["artifacts_sha256"].items()):
            write_current(prior, output, True)
            print(f"CACHE HIT: {len(prior['builds'])} validated builds; no compilation, execution or model calls")
            return
    output.mkdir(parents=True, exist_ok=True)
    tests = vectors()
    (output / "vectors.json").write_text(json.dumps(tests, indent=2) + "\n")
    payload = "".join(item["hex"] + "\n" for item in tests)
    builds = []
    for standard in ("c++11", "c++14"):
        for abi in (0, 1):
            name = f"{standard.replace('+', 'p')}-string-abi{abi}"
            build = output / name
            build.mkdir(exist_ok=True)
            symbols = build / "target-with-symbols"
            compilation_key = hashlib.sha256(json.dumps({"authoring": {p: h for p, h in source_hashes.items() if p.startswith("authoring/")},
                "compiler_dependencies": dependencies, "standard": standard, "abi": abi, "flags": "pedantic/Wall/Wextra/Werror/O2/g0/no-pie"}, sort_keys=True).encode()).hexdigest()
            compilation = SUITE / "private/compiled-cache" / compilation_key
            compilation.mkdir(parents=True, exist_ok=True)
            cached_binary = compilation / "target-with-symbols"
            command = [compiler, f"-std={standard}", "-pedantic-errors", "-Wall", "-Wextra", "-Werror",
                       "-O2", "-g0", "-no-pie", f"-D_GLIBCXX_USE_CXX11_ABI={abi}",
                       str(SUITE / "authoring/containers.cpp"), str(SUITE / "authoring/driver.cpp"), "-o", str(cached_binary)]
            compile_receipt = compilation / "receipt.json"
            compilation_cached = compile_receipt.exists() and cached_binary.exists() and json.loads(compile_receipt.read_text())["binary_sha256"] == digest(cached_binary)
            if not compilation_cached:
                try:
                    compiled = run(command)
                except subprocess.CalledProcessError as error:
                    (compilation / "compile.log").write_text(error.stdout + error.stderr)
                    raise RuntimeError(error.stderr) from error
                (compilation / "compile.log").write_text(compiled.stdout + compiled.stderr)
                compile_receipt.write_text(json.dumps({"binary_sha256": digest(cached_binary), "command": command}, indent=2) + "\n")
            shutil.copy2(cached_binary, symbols)
            shutil.copy2(compilation / "compile.log", build / "compile.log")
            target = build / "target"
            shutil.copyfile(symbols, target)
            target.chmod(0o755)
            run(["strip", "--strip-all", str(target)])
            sections = run(["readelf", "-SW", str(target)]).stdout
            assert ".symtab" not in sections and ".debug_info" not in sections
            expected = [item["expected"][standard] for item in tests]
            text_hashes = []
            for binary in (symbols, target):
                actual = run([str(binary)], input=payload).stdout.splitlines()
                if actual != expected:
                    mismatch = next((i for i, (a, b) in enumerate(zip(actual, expected)) if a != b), min(len(actual), len(expected)))
                    raise RuntimeError(f"{name} failed check {mismatch}: {actual[mismatch:mismatch+1]} vs {expected[mismatch:mismatch+1]}")
                text = build / f"{binary.name}.text"
                run(["objcopy", "--dump-section", f".text={text}", str(binary)])
                text_hashes.append(digest(text))
            assert text_hashes[0] == text_hashes[1]
            # A constant error answer must fail. Correct output is not a compile-only control.
            wrong_passed = sum(value == "ERR:POINT" for value in expected)
            assert wrong_passed < len(tests) // 100
            entry_lines = run(["nm", "-n", "-C", str(symbols)]).stdout.splitlines()
            entries = {}
            for line in entry_lines:
                if " T stl_benchmark::" in line and "_workload" in line:
                    entries[line.split("stl_benchmark::", 1)[1].split("(")[0].split("[")[0]] = "0x" + line.split()[0].lstrip("0")
            assert set(entries) == {name + "_workload" for name in POINT_NAMES}
            entries = {str(point): entries[name + "_workload"] for point, name in enumerate(POINT_NAMES)}
            manifest = {"id": name, "standard": standard, "string_abi": abi, "passed": len(tests), "total": len(tests),
                "active_points": 21 if standard == "c++11" else 22, "cpp14_point_is_standard_error_control": standard == "c++11",
                "regular_symbols_removed": True, "text_unchanged_after_strip": True,
                "binary_sha256": digest(target), "entries": entries, "command": command, "compilation_cache_hit": compilation_cached,
                "negative_control_passed_checks": wrong_passed, "profile": "optimized-stripped"}
            (build / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            builds.append(manifest)
            print(f"{name}: both copies pass {len(tests)} checks; {len(entries)} reachable handlers", flush=True)
    artifacts = {str(p.relative_to(output)): digest(p) for p in sorted(output.rglob("*")) if p.is_file() and p != receipt}
    result = {"passed": True, "fingerprint": fingerprint, "created_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "compiler": version.splitlines()[0], "compiler_sha256": digest(compiler_path), "source_hashes": source_hashes,
              "toolchain_dependency_digest": hashlib.sha256(json.dumps(dependencies, sort_keys=True).encode()).hexdigest(),
              "builds": builds, "vectors": len(tests), "artifacts_sha256": artifacts, "live_model_calls": 0,
              "scope": "All 16 C++11 container templates/adaptors, vector<bool>, string/bitset/valarray and grammar. Not exhaustive API coverage."}
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    write_current(result, output, False)


def write_current(receipt, output, cache_hit):
    public = SUITE / "public"
    public.mkdir(exist_ok=True)
    for build in receipt["builds"]:
        destination = public / build["id"]
        destination.mkdir(exist_ok=True)
        for name in ("target",):
            source = output / build["id"] / name
            if not (destination / name).exists() or digest(destination / name) != digest(source):
                shutil.copy2(source, destination / name)
        (destination / "manifest.json").write_text(json.dumps({key: build[key] for key in
            ("id", "standard", "string_abi", "entries", "binary_sha256", "profile", "regular_symbols_removed")}, indent=2) + "\n")
        (destination / "prompt.txt").write_text(
            f"Recover this optimized stripped Linux x86-64 ELF as readable {build['standard']} code.\n"
            "Implement std::string reconstructed(const std::vector<std::uint8_t>& data); no main.\n"
            "First byte selects handler 0..21; remaining bytes are signed i16LE records. Empty and malformed inputs matter.\n"
            "Use only the selected target through read-only REA tools; no source, hidden tests, execution or prior answers.\n"
            "Return exactly the same string. Supply one cpp code block and evidence addresses, then close the binary.\n"
            f"This target uses libstdc++ _GLIBCXX_USE_CXX11_ABI={build['string_abi']}.\n"
            f"Handler entry hints (names replaced): {json.dumps(build['entries'])}\n")
    (SUITE / "private" / "validation.json").write_text(json.dumps({**receipt, "cache_directory": str(output), "cache_hit": cache_hit}, indent=2) + "\n")


if __name__ == "__main__":
    main()
