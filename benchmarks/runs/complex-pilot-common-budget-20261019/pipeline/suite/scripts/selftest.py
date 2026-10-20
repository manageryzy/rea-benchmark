"""Positive, negative, and language-rejection controls for the native scorer."""

import importlib.util
import json
from pathlib import Path
import sys

SUITE = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("complex_grader", SUITE / "scripts/grade.py")
grader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(grader)


def standalone(language):
    sources = SUITE / "authoring" / language
    code = (sources / ("case." + {"cpp": "cpp", "rust": "rs", "go": "go"}[language])).read_text()
    if language == "cpp":
        header = (sources / "abi.hpp").read_text().replace("#pragma once", "")
        bridge = (sources / "abi.cpp").read_text().replace('#include "abi.hpp"', "")
        code = header + bridge + code.replace('#include "abi.hpp"', "")
    elif language == "rust":
        bridge = "\n".join(line for line in (sources / "abi_bridge.rs").read_text().splitlines() if not line.startswith("//!"))
        code = "mod abi_bridge {\n" + bridge + "\n}\n" + code
    return code


def main():
    rows = []
    base = SUITE / "private/grader-validation"
    for language in ["cpp", "rust", "go"]:
        for kind in ["correct", "incorrect", "wrong-language"]:
            directory = base / language / kind
            directory.mkdir(parents=True, exist_ok=True)
            code = standalone(language) if kind == "correct" else {
                "cpp": '#include <string>\n#include <span>\n#include <cstdint>\nstd::string reconstructed(std::span<const std::uint8_t>) { return "ERR:POINT"; }\n',
                "rust": 'pub fn reconstructed(_: &[u8]) -> String { "ERR:POINT".into() }\n',
                "go": 'package main\nfunc reconstructed(data []byte) string { return "ERR:POINT" }\n',
            }[language]
            tag = "python" if kind == "wrong-language" else language
            (directory / "answer.md").write_text(f"```{tag}\n{code}\n```\n")
            result = grader.grade(directory, language)
            if kind == "correct":
                assert result["program_success"], (language, result)
            elif kind == "incorrect":
                assert result["compiled"] and result["passed"] == 16 and result["fully_correct_points"] == 0
            else:
                assert not result["compiled"] and "code block" in result["failure"]
            rows.append({"language": language, "control": kind, "compiled": result["compiled"],
                         "passed": result["passed"], "total": result["total"], "failure": result["failure"]})
            print(f"{language} {kind}: {result['passed']}/{result['total']}; control passed", flush=True)
    (base / "validation.json").write_text(json.dumps({"passed": True, "live_model_calls": 0, "controls": rows}, indent=2) + "\n")


if __name__ == "__main__":
    main()
