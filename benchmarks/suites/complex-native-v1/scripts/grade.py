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
    """Versioned exact-byte grading; preserve any original grade and candidate."""
    import sys
    sys.path.insert(0, str(BENCH / 'scripts'))
    from grading.store import grade_entry
    return grade_entry(directory, language, 'complex')


if __name__ == "__main__":
    print(json.dumps(grade(sys.argv[1]), indent=2))
