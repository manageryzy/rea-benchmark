"""Cache feature probes; execute an independent oracle if a facility exists."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from oracle import vectors, joined

SUITE = Path(__file__).resolve().parents[1]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    compiler = os.environ.get("CXX", "/usr/bin/g++")
    version = subprocess.check_output([compiler, "--version"], text=True)
    source = SUITE / "authoring/modern.cpp"
    driver = SUITE / "authoring/driver.cpp"
    files = [source, driver, Path(__file__), SUITE / "scripts/oracle.py"]
    # Reuse the core builder's standard-library dependency fingerprint.
    core = json.loads((SUITE / "private/validation.json").read_text())
    key = hashlib.sha256(json.dumps({"sources": {str(p): sha(p) for p in files}, "compiler": version,
        "compiler_binary": sha(Path(shutil.which(compiler)).resolve()), "core_toolchain": core["toolchain_dependency_digest"]}, sort_keys=True).encode()).hexdigest()
    directory = SUITE / "private/modern-cache" / key
    receipt = directory / "validation.json"
    current = SUITE / "private/modern-validation.json"
    if receipt.exists():
        saved = json.loads(receipt.read_text())
        if all(sha(directory / name) == digest for name, digest in saved["artifacts_sha256"].items()):
            current.write_text(json.dumps({**saved, "cache_hit": True}, indent=2) + "\n")
            print("CACHE HIT: later-standard feature probes; no recompilation or model calls")
            return
    directory.mkdir(parents=True, exist_ok=True)
    rows = []
    for kind, name in enumerate(["flat_map", "flat_multimap", "flat_set", "flat_multiset", "inplace_vector", "hive"]):
        standard = "c++23" if kind < 4 else "c++2c"
        target = directory / f"target-{name}"
        command = [compiler, f"-std={standard}", "-O2", "-g0", "-pedantic-errors", "-Wall", "-Wextra", "-Werror",
                   f"-DCONTAINER_KIND={kind}", str(source), str(driver), "-o", str(target)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
        (directory / f"{name}.log").write_text(result.stdout + result.stderr)
        row = {"container": name, "standard": "C++23" if kind < 4 else "C++26", "command": command,
               "status": "unsupported_by_installed_toolchain" if result.returncode else "built", "compiler_exit": result.returncode,
               "diagnostic": (result.stderr or result.stdout)[:1200], "model_runs": 0}
        if not result.returncode:
            reference_point = [7, 8, 5, 6, 1, 3][kind]
            cases = [dict(item) for item in vectors() if item["point"] == reference_point]
            expected = []
            for item in cases:
                data = bytes.fromhex(item["hex"])
                item["hex"] = (b"\x00" + data[1:]).hex()
                if len(data) % 2 == 0: answer = "ERR:LEN"
                elif kind < 4: answer = item["expected"]["c++14"]
                else:
                    import struct
                    values = list(struct.unpack("<" + "h" * ((len(data) - 1) // 2), data[1:]))
                    answer = ("ERR:CAP" if len(values) > 64 else joined(values[::-1])) if kind == 4 else joined(sorted(abs(x) for x in values))
                expected.append(answer)
            unstripped = directory / f"target-{name}-with-symbols"
            shutil.copy2(target, unstripped)
            subprocess.run(["strip", "--strip-all", str(target)], check=True)
            payload = "".join(item["hex"] + "\n" for item in cases)
            for binary in (unstripped, target):
                actual = subprocess.check_output([str(binary)], input=payload, text=True, timeout=20).splitlines()
                assert actual == expected, name
            row.update(status="locally_verified", checks=len(cases), binary_sha256=sha(target))
        print(f"{name}: {row['status']}", flush=True)
        rows.append(row)
    artifacts = {str(p.relative_to(directory)): sha(p) for p in directory.iterdir() if p.is_file() and p != receipt}
    result = {"passed": True, "meaning": "All feature probes completed; unavailable containers have NOT been behavior-tested.",
        "fingerprint": key, "compiler": version.splitlines()[0], "rows": rows, "cache_directory": str(directory),
        "artifacts_sha256": artifacts, "cache_hit": False, "live_model_calls": 0}
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    current.write_text(receipt.read_text())


if __name__ == "__main__":
    main()
