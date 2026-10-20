"""Grade unchanged native submissions with point-level diagnostics and isolation."""

import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

SUITE = Path(__file__).resolve().parents[1]
BENCH = SUITE.parents[1]
base_spec = importlib.util.spec_from_file_location("native_grader", BENCH / "scripts/grade.py")
native = importlib.util.module_from_spec(base_spec)
base_spec.loader.exec_module(native)
sys.path.insert(0, str(BENCH / "scripts"))
from collect_answer import collect

CPP_DRIVER = native.CPP_DRIVER.replace(
    'printf("%lld\\n",(long long)reconstructed(std::span<const std::uint8_t>(bytes,count)));',
    'std::cout << reconstructed(std::span<const std::uint8_t>(bytes,count)) << "\\n";')
CPP_DRIVER = CPP_DRIVER.replace('#include <span>', '#include <span>\n#include <iostream>')
CPP_DRIVER = CPP_DRIVER.replace("char line[2048]; uint8_t bytes[1024];", "char line[16384]; uint8_t bytes[8192];")


def grade(directory, language=None):
    directory = Path(directory).resolve()
    if language is None:
        language = json.loads((directory / "result.json").read_text())["output_language"]
    answer = collect(directory) if (directory / "events.jsonl").exists() else (directory / "answer.md").read_text()
    blocks = re.findall(r"^```([^\n]*)\n(.*?)^```\s*$", answer, re.M | re.S)
    fence, filename = native.LANGUAGES[language]
    matching = [code for tag, code in blocks if re.fullmatch(fence, tag.strip(), re.I)]
    vectors_path = SUITE / "private/vectors.json"
    cases = json.loads(vectors_path.read_text())
    result = {
        "output_language": language, "compiled": False, "passed": 0, "total": len(cases),
        "pass_rate": 0, "failure": None, "points": {}, "categories": {},
        "vectors_sha256": hashlib.sha256(vectors_path.read_bytes()).hexdigest(),
        "candidate_modified": False,
    }
    for case in cases:
        for field, key in [("points", case["point"]), ("categories", case["point"] + ":" + case["category"])]:
            group = result[field].setdefault(key, {"passed": 0, "total": 0})
            group["total"] += 1
    if len(blocks) != 1 or len(matching) != 1:
        result["failure"] = f"Expected one {language} code block, got {len(matching)} matching/{len(blocks)} total"
    else:
        code = matching[0]
        extension = filename.rsplit(".", 1)[1]
        (directory / f"reconstructed.{extension}").write_text(code)
        result["candidate_source_sha256"] = hashlib.sha256(code.encode()).hexdigest()
        if language == "go" and re.search(r'"(?:unsafe|C)"', code):
            result["failure"] = "forbidden_unsafe_or_ffi"
        else:
            artifact = directory / "grade"
            artifact.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="rea-complex-grade-") as temporary:
                work = Path(temporary)
                (work / filename).write_text(code)
                driver, content, command = {
                    "cpp": ("driver.cpp", CPP_DRIVER, ["g++", "-std=c++20", "-O2", "-Wall", "-Wextra", "driver.cpp", "-o", "candidate"]),
                    "rust": ("driver.rs", native.RUST_DRIVER, ["/opt/rust/bin/rustc", "--sysroot", "/opt/rust", "--edition=2021", "-C", "linker=/usr/bin/gcc", "-F", "unsafe_code", "-C", "opt-level=2", "driver.rs", "-o", "candidate"]),
                    "go": ("driver.go", native.GO_DRIVER, ["/opt/go/bin/go", "build", "-trimpath", "-ldflags=-s -w", "-o", "candidate", "driver.go", "candidate.go"]),
                }[language]
                (work / driver).write_text(content)
                for path in [work / filename, work / driver]:
                    shutil.copy2(path, artifact / path.name)
                result["compile_command"] = command
                try:
                    completed = subprocess.run(native.sandbox(work, command, language), env=native.clean_environment(),
                                               text=True, capture_output=True, timeout=120,
                                               preexec_fn=native.resource_limits(language, True))
                    (directory / "compile.log").write_text((completed.stdout + completed.stderr)[:262144])
                    if completed.returncode:
                        result["failure"] = "compile_error"
                    else:
                        result["compiled"] = True
                        shutil.copy2(work / "candidate", artifact / "candidate")
                        shutil.rmtree(work / "go-cache", ignore_errors=True)
                        completed = subprocess.run(native.sandbox(work, ["/work/candidate"], language),
                                                   env=native.clean_environment(), text=True, capture_output=True,
                                                   input="".join(case["hex"] + "\n" for case in cases), timeout=20,
                                                   preexec_fn=native.resource_limits(language))
                        (directory / "candidate-stderr.log").write_text(completed.stderr[:65536])
                        actual = completed.stdout.splitlines()
                        (artifact / "actual.json").write_text(json.dumps(actual, indent=2) + "\n")
                        if completed.returncode:
                            result["failure"] = f"candidate_exit_{completed.returncode}"
                        if len(actual) != len(cases):
                            result["failure"] = result["failure"] or "wrong_output_count"
                        mismatches = []
                        for index, case in enumerate(cases):
                            value = actual[index] if index < len(actual) else None
                            passed = value == case["expected"]
                            result["passed"] += int(passed)
                            result["points"][case["point"]]["passed"] += int(passed)
                            result["categories"][case["point"] + ":" + case["category"]]["passed"] += int(passed)
                            if not passed and len(mismatches) < 24:
                                mismatches.append({"index": index, "point": case["point"], "category": case["category"],
                                                   "expected": case["expected"], "actual": value})
                        result["first_mismatches"] = mismatches
                except subprocess.TimeoutExpired:
                    result["failure"] = "candidate_timeout" if result["compiled"] else "compile_timeout"
    result["pass_rate"] = result["passed"] / result["total"]
    result["fully_correct_points"] = sum(group["passed"] == group["total"] for key, group in result["points"].items() if key != "dispatch")
    result["total_function_points"] = 6
    result["program_success"] = result["compiled"] and result["passed"] == result["total"] and result["failure"] is None
    (directory / "grade.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    print(json.dumps(grade(sys.argv[1]), indent=2))
