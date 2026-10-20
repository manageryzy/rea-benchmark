.DEFAULT_GOAL := reports
PYTHON ?= /usr/bin/python3
CONFIG ?= benchmarks/config/benchmark.json
BENCHMARK_FLAGS ?=

.PHONY: setup benchmark reports report reports-public check-public benchmark-plan benchmark-run check check-runner check-reports check-environment check-paging build-cpp-points archive-profiles audit-environment audit-condition clean-reports list help review-prepare build-fixtures check-fixtures verify-fixtures

# A clean checkout starts stopped. Existing local state, including a running
# batch, is never overwritten by setup or by report generation.
setup:
	$(PYTHON) -c 'from pathlib import Path; p = Path("benchmarks/config/execution.json"); p.exists() or p.write_bytes(Path("benchmarks/config/execution.example.json").read_bytes())'

benchmark-plan benchmark benchmark-run reports report check: setup

benchmark-plan:
	$(PYTHON) benchmarks/scripts/run.py plan --config "$(CONFIG)" $(BENCHMARK_FLAGS)

benchmark benchmark-run:
	$(PYTHON) benchmarks/scripts/run.py run --config "$(CONFIG)" --resume-experiments $(BENCHMARK_FLAGS)

reports report:
	$(PYTHON) benchmarks/scripts/report.py build

reports-public:
	$(PYTHON) benchmarks/scripts/report.py public

check-public:
	$(PYTHON) benchmarks/scripts/check_public_report.py

check:
	$(PYTHON) benchmarks/scripts/check_structure.py
	$(PYTHON) benchmarks/scripts/check_gpt_models.py
	$(PYTHON) benchmarks/scripts/check_runner.py
	$(PYTHON) benchmarks/scripts/check_environment.py
	$(PYTHON) benchmarks/scripts/check_paging.py
	$(PYTHON) benchmarks/scripts/check_reports.py

check-paging:
	$(PYTHON) benchmarks/scripts/check_paging.py

check-runner:
	$(PYTHON) benchmarks/scripts/check_runner.py

review-prepare:
	$(PYTHON) benchmarks/scripts/review_readability.py prepare

build-fixtures:
	$(PYTHON) benchmarks/suites/record-parser/scripts/build.py
	$(PYTHON) benchmarks/suites/complex-native-v1/scripts/build.py
	$(PYTHON) benchmarks/suites/stl-native-v2/scripts/build.py

check-fixtures:
	$(PYTHON) benchmarks/suites/record-parser/grading/selftest.py
	$(PYTHON) benchmarks/suites/complex-native-v1/scripts/selftest.py
	$(PYTHON) benchmarks/suites/stl-native-v2/scripts/selftest.py

verify-fixtures:
	.tools/node-v24.22.0-linux-x64/bin/node benchmarks/suites/record-parser/scripts/verify-runtime.mjs
	REA_BENCHMARK_CONFIG=benchmarks/suites/complex-native-v1/config/pilot-common-budget.json .tools/node-v24.22.0-linux-x64/bin/node benchmarks/suites/complex-native-v1/scripts/verify.mjs
	.tools/node-v24.22.0-linux-x64/bin/node benchmarks/suites/complex-native-v1/scripts/verify-data-tools.mjs

build-cpp-points:
	$(PYTHON) benchmarks/scripts/build_cpp_points.py
	$(PYTHON) benchmarks/scripts/probe_cpp_features.py

check-environment:
	$(PYTHON) benchmarks/scripts/check_environment.py

check-reports:
	$(PYTHON) benchmarks/scripts/check_reports.py

audit-environment:
	$(PYTHON) benchmarks/scripts/audit_environment.py

audit-condition:
	$(PYTHON) benchmarks/scripts/audit_condition.py

archive-profiles:
	$(PYTHON) benchmarks/scripts/archive_profiles.py

clean-reports:
	$(PYTHON) benchmarks/scripts/report.py clean

list:
	./bin/benchmark list

help:
	@echo "make benchmark      All configured models/cases; validate caches, run gaps, grade, audit and generate global reports"
	@echo "make benchmark BENCHMARK_FLAGS=--offline  Require complete valid inference caches; zero provider launches"
	@echo "make benchmark-plan CONFIG=path.json     Preview configuration without model calls"
	@echo "make reports        Rebuild every HTML/JSON/CSV report offline from saved records"
	@echo "make reports-public Rebuild HTML-only Pages output from the sanitized summary cache; no raw data or model calls"
	@echo "make check-public   Validate privacy boundaries, global metric parity, links and reproducible public builds"
	@echo "make check          Check artifacts, offline evidence replay, client transports and report reproducibility"
	@echo "make check-runner   Verify cache reuse and missing-cache dispatch using offline fixtures"
	@echo "make check-reports  Rebuild twice and compare output hashes and local links"
	@echo "make check-environment Replay cached evidence offline through both adapters; no model calls"
	@echo "make audit-environment Audit cached DSH sessions offline; no model calls"
	@echo "make audit-condition Audit saved fixed-harness inputs; no model calls"
	@echo "make build-cpp-points Validate GCC/Clang compiler, optimization and language-feature builds; no model calls"
	@echo "make archive-profiles Preserve the exact source/configuration versions of saved attempts; no model calls"
	@echo "make clean-reports  Remove generated report output only"
	@echo "make list           Show registered suites, models and saved experiment status"
