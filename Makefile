.DEFAULT_GOAL := reports
PYTHON ?= /usr/bin/python3
CONFIG ?= benchmarks/config/benchmark.json
BENCHMARK_FLAGS ?=
# Optional private Chrome/Perfetto trace; no prompts, credentials or model output.
TRACE ?= auto
PROFILE_FLAGS ?=
TEST_JOBS ?= 4
BROWSER_NODE ?= .tools/node-v24.22.0-linux-x64/bin/node
WARM_BUDGET_SECONDS ?= 180

.PHONY: check-analysis-context setup setup-grading setup-c-executor check-c-executor setup-capabilities build-capabilities prepare-capabilities check-capabilities check-structured-grading check-buffer-capabilities check-capability-coordinator benchmark reports report reports-public pages check-public benchmark-plan benchmark-run check check-runner check-reports check-environment check-paging build-cpp-points archive-profiles audit-environment audit-condition clean-reports list help review-prepare build-fixtures check-fixtures verify-fixtures check-measurement check-action-cache regrade attest-runtime check-attestation check-requests check-grading-runtime
.PHONY: check-abi-probes
.PHONY: check-pointer-probes
.PHONY: check-variant-objects
.PHONY: check-indirect-calls
.PHONY: setup-optimized-locations build-optimized-locations check-optimized-locations
.PHONY: build-native-abi check-native-abi
.PHONY: setup-matched-baselines build-matched-baselines check-matched-baselines check-rust-buffer check-go-buffer
.PHONY: check-cpp-buffer
.PHONY: check-capability-measurements
.PHONY: check-capability-registration check-parallel-controls
.PHONY: check-lifecycle-capabilities
.PHONY: check-cpp-lifecycle
.PHONY: check-report-review check-report-browser
.PHONY: profile-preparation check-selection

check-selection:
	$(PYTHON) benchmarks/scripts/check_deferred_selection.py

.PHONY: check-read-cache
check-read-cache:
	$(PYTHON) benchmarks/scripts/check_shared_json.py
	$(PYTHON) benchmarks/scripts/check_read_session.py
	$(PYTHON) benchmarks/scripts/check_benchmark_trace.py

# Offline: existing frozen inputs only; the probe forbids network and subprocesses.
profile-preparation:
	$(PYTHON) benchmarks/scripts/benchmark_trace.py --accelerate --detail --no-render --output "$(TRACE)" -- benchmarks/scripts/profile_preparation.py $(PROFILE_FLAGS)

.PHONY: profile-startup
profile-startup:
	$(PYTHON) benchmarks/scripts/profile_startup.py $(PROFILE_FLAGS)

.PHONY: profile-parallel
profile-parallel:
	$(PYTHON) benchmarks/scripts/profile_parallel.py $(PROFILE_FLAGS)

.PHONY: prepare-grading-images
prepare-grading-images:
	$(PYTHON) benchmarks/scripts/prepare_grading_images.py

.PHONY: check-startup-policy
check-startup-policy:
	$(PYTHON) benchmarks/scripts/check_startup_policy.py

.PHONY: check-model-concurrency
check-model-concurrency:
	$(PYTHON) benchmarks/scripts/check_model_concurrency.py

.PHONY: check-native-attempts
check-native-attempts:
	$(PYTHON) benchmarks/scripts/check_native_attempts.py

# Focused report-only controls; no renderer, benchmark or model dependency.
check-report-review:
	$(PYTHON) benchmarks/scripts/check_report_views.py
	$(PYTHON) benchmarks/scripts/check_report_budget.py
	$(PYTHON) benchmarks/scripts/check_report_dimensions.py
	$(PYTHON) benchmarks/scripts/check_report_charts_review.py
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m unittest \
		check_public_report.PublicAcceptance.test_missing_token_categories_remain_unknown_and_zero_is_preserved \
		check_public_report.PublicAcceptance.test_request_coverage_distinguishes_missing_usage_and_proven_zero \
		check_public_report.PublicAcceptance.test_sanitized_pricing_dates_rates_and_missing_invoice_are_visible \
		check_public_report.PublicAcceptance.test_all_native_states_project_and_budget_stop_is_ungraded

check-report-browser:
	$(BROWSER_NODE) benchmarks/scripts/check_report_browser.cjs $(BROWSER_FLAGS)

# A clean checkout starts stopped. Existing local state, including a running
# batch, is never overwritten by setup or by report generation.
setup:
	$(PYTHON) -c 'from pathlib import Path; p = Path("benchmarks/config/execution.json"); p.exists() or p.write_bytes(Path("benchmarks/config/execution.example.json").read_bytes())'

benchmark-plan benchmark benchmark-run reports report: setup
regrade check-measurement check-grading-runtime: setup-grading

setup-grading:
	$(PYTHON) benchmarks/scripts/grading/toolchains.py

setup-c-executor:
	$(PYTHON) benchmarks/scripts/grading/toolchains.py --language c
	$(PYTHON) benchmarks/scripts/grading/c_executor/setup.py

check-c-executor:
	$(PYTHON) benchmarks/scripts/check_c_executor.py

setup-capabilities:
	$(PYTHON) benchmarks/scripts/grading/toolchains.py --language cpp
	$(PYTHON) benchmarks/scripts/capabilities/build.py --setup

build-capabilities: setup-capabilities
	$(PYTHON) benchmarks/scripts/capabilities/build.py

check-capabilities: setup-capabilities
	$(PYTHON) benchmarks/scripts/check_capabilities.py

prepare-capabilities: setup-capabilities setup-grading setup-optimized-locations setup-matched-baselines
	$(PYTHON) benchmarks/scripts/run.py prepare --suite capabilities-v1

check-structured-grading:
	$(PYTHON) benchmarks/scripts/check_structured_grading.py

check-buffer-capabilities: setup-capabilities
	$(PYTHON) benchmarks/scripts/check_buffer_capabilities.py

check-lifecycle-capabilities: setup-capabilities setup-c-executor
	$(PYTHON) benchmarks/scripts/check_lifecycle_capabilities.py

check-cpp-lifecycle: setup-capabilities setup-c-executor
	$(PYTHON) benchmarks/scripts/check_cpp_lifecycle.py

check-abi-probes: setup-capabilities
	$(PYTHON) benchmarks/scripts/check_abi_probes.py

check-pointer-probes: setup-capabilities setup-c-executor
	$(PYTHON) benchmarks/scripts/check_pointer_probes.py

check-variant-objects: setup-capabilities setup-c-executor
	$(PYTHON) benchmarks/scripts/check_variant_objects.py

check-indirect-calls: setup-capabilities setup-c-executor
	$(PYTHON) benchmarks/scripts/check_indirect_calls.py

setup-optimized-locations: setup-capabilities
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m capabilities.optimized_locations.build --setup

build-optimized-locations: setup-optimized-locations
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m capabilities.optimized_locations.build

check-optimized-locations: setup-optimized-locations
	$(PYTHON) benchmarks/scripts/check_optimized_locations.py

build-native-abi: setup-capabilities setup-grading
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m capabilities.native_abi.build

check-native-abi: setup-capabilities setup-grading setup-c-executor
	$(PYTHON) benchmarks/scripts/check_native_abi.py

setup-matched-baselines: setup-capabilities setup-grading
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m capabilities.baselines.build --setup

build-matched-baselines: setup-matched-baselines
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m capabilities.baselines.build

check-matched-baselines: setup-matched-baselines setup-c-executor
	$(PYTHON) benchmarks/scripts/check_matched_baselines.py

check-rust-buffer: setup-matched-baselines setup-c-executor
	$(PYTHON) benchmarks/scripts/check_rust_buffer.py

check-go-buffer: setup-matched-baselines setup-c-executor
	$(PYTHON) benchmarks/scripts/check_go_buffer.py

check-cpp-buffer: check-buffer-capabilities prepare-capabilities
	$(PYTHON) benchmarks/scripts/check_cpp_buffer.py

check-parallel-controls:
	$(PYTHON) benchmarks/scripts/check_parallel_controls.py

check-capability-coordinator:
	$(PYTHON) benchmarks/scripts/check_capability_coordinator.py --jobs $(TEST_JOBS)

.PHONY: check-capability-incremental
check-capability-incremental:
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- $(PYTHON) benchmarks/scripts/incremental_validation.py --jobs $(TEST_JOBS)

.PHONY: check-capability-coordinator-cold check-validation-cache
check-capability-coordinator-cold: prepare-capabilities check-parallel-controls
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- $(PYTHON) benchmarks/scripts/check_analysis_context.py
	$(PYTHON) benchmarks/scripts/check_capability_coordinator.py --jobs $(TEST_JOBS) --uncached

check-validation-cache:
	$(PYTHON) benchmarks/scripts/check_validation_cache.py

check-capability-measurements:
	$(PYTHON) benchmarks/scripts/check_capability_measurements.py

check-capability-registration: prepare-capabilities setup-c-executor
	$(PYTHON) benchmarks/scripts/check_capability_registration.py

benchmark-plan:
	$(PYTHON) benchmarks/scripts/run.py plan --config "$(CONFIG)" $(BENCHMARK_FLAGS)

benchmark benchmark-run:
	$(PYTHON) benchmarks/scripts/benchmark_trace.py --staged --no-render $(TRACE_FLAGS) --output "$(TRACE)" -- benchmarks/scripts/run.py run --config "$(CONFIG)" --resume-experiments --runtime-ready $(BENCHMARK_FLAGS)

.PHONY: trace-view
trace-view:
	$(PYTHON) benchmarks/scripts/benchmark_trace.py --render "$(TRACE)"

reports report:
	$(PYTHON) benchmarks/scripts/report.py build

reports-public:
	$(PYTHON) benchmarks/scripts/report.py public

pages:
	$(PYTHON) benchmarks/scripts/report.py pages

check-public:
	$(PYTHON) benchmarks/scripts/check_public_report.py

check-action-cache:
	$(PYTHON) benchmarks/scripts/check_action_cache.py
	$(PYTHON) benchmarks/scripts/check_attempt_routing.py

attest-runtime:
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- $(PYTHON) benchmarks/scripts/runtime_attestation.py inventory

.PHONY: setup-local-runtime
setup-local-runtime:
	$(PYTHON) benchmarks/scripts/runtime_storage.py setup $(if $(RUNTIME_REFRESH),--refresh,)

check-analysis-context:
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- $(PYTHON) benchmarks/scripts/check_analysis_context.py

check-attestation:
	$(PYTHON) benchmarks/scripts/check_attestation.py

check-requests:
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- $(PYTHON) benchmarks/scripts/check_requests.py

.PHONY: check-stream-handoff
check-stream-handoff:
	$(PYTHON) benchmarks/scripts/check_stream_handoff.py

# Wallet/evidence controls are offline and do not populate the 104-check suite.
.PHONY: check-wallet-evidence
check-wallet-evidence:
	$(BROWSER_NODE) --test benchmarks/scripts/check_compact_paging.mjs benchmarks/scripts/check_lean_paging.mjs
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m unittest check_wallet_budget check_stream_handoff check_evidence_policy check_capability_paging check_report_budget

.PHONY: check-response-allowance
check-response-allowance:
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m unittest check_response_allowance check_wallet_budget check_stream_handoff

.PHONY: check-budget-runner
check-budget-runner:
	$(PYTHON) benchmarks/scripts/check_budget_runner.py

# Focused evidence/budget regression controls only: no model, REA analysis,
# native grading, full coordinator validation, or report generation.
.PHONY: check-harness-fixes
check-harness-fixes:
	$(BROWSER_NODE) --test benchmarks/scripts/check_compact_paging.mjs
	$(PYTHON) benchmarks/scripts/check_evidence_policy.py
	$(PYTHON) benchmarks/scripts/check_capability_paging.py
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m unittest check_requests.ContractTests check_requests.BudgetCompletionTests
	$(PYTHON) benchmarks/scripts/check_budget_runner.py
	$(PYTHON) benchmarks/scripts/check_report_budget.py
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m unittest \
		check_capability_measurements.MeasurementControls.test_completed_over_budget_observation_keeps_grade_and_spending \
		check_capability_measurements.MeasurementControls.test_partial_over_budget_observation_cannot_gain_grade \
		check_capability_measurements.MeasurementControls.test_exact_threshold_observation_keeps_grade_but_holds_queue \
		check_capability_measurements.MeasurementControls.test_27_budget_exhaustion_stays_ungraded_and_keeps_observed_spending

check-measurement:
	$(PYTHON) benchmarks/scripts/check_measurement.py

check-grading-runtime:
	$(PYTHON) benchmarks/scripts/check_grading_runtime.py

regrade: setup
	$(PYTHON) benchmarks/scripts/run.py regrade --config "$(CONFIG)" --all-saved $(BENCHMARK_FLAGS)

check-cold:
	$(PYTHON) benchmarks/scripts/timed_checks.py start
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- make setup setup-grading setup-capabilities setup-optimized-locations setup-matched-baselines
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- make check-requests
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_attestation.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_action_cache.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_grading_runtime.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_c_executor.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_capabilities.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_structured_grading.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_buffer_capabilities.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_lifecycle_capabilities.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_cpp_lifecycle.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_abi_probes.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_pointer_probes.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_variant_objects.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_indirect_calls.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_optimized_locations.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_native_abi.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_matched_baselines.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_rust_buffer.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_go_buffer.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/run.py prepare --suite capabilities-v1
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_cpp_buffer.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_parallel_controls.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_warm_controls.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- make check-analysis-context
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_capability_coordinator.py --jobs $(TEST_JOBS)
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_capability_registration.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_capability_measurements.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_measurement.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_structure.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_gpt_models.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_runner.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_environment.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_paging.py
	$(PYTHON) benchmarks/scripts/timed_checks.py run -- $(PYTHON) benchmarks/scripts/check_reports.py

	$(PYTHON) benchmarks/scripts/timed_checks.py finish

check-paging:
	$(PYTHON) benchmarks/scripts/check_paging.py

check-runner:
	$(PYTHON) benchmarks/scripts/check_runner.py

.PHONY: check-warm check-cold check-warm-controls
check: check-warm

check-warm:
	$(PYTHON) benchmarks/scripts/check_warm_cache.py --budget-seconds $(WARM_BUDGET_SECONDS)

check-warm-controls:
	$(PYTHON) benchmarks/scripts/check_warm_controls.py

# Optional explicit AI reviews; never dependencies of benchmark/reports.
REVIEW_FLAGS ?=
.PHONY: review-apply
review-prepare:
	$(PYTHON) benchmarks/scripts/review_readability.py prepare $(REVIEW_FLAGS)
review-apply:
	@test -n "$(DECISIONS)" || (echo "Set DECISIONS to an explicit review decisions file"; exit 2)
	$(PYTHON) benchmarks/scripts/review_readability.py apply --decisions "$(DECISIONS)" $(REVIEW_FLAGS)

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
	@echo "make pages          Regenerate public HTML locally and stage docs/ for branch-based GitHub Pages"
	@echo "make check-public   Validate privacy boundaries, global metric parity, links and reproducible public builds"
	@echo "make check-action-cache  Verify action/CAS integrity and unrelated-change invariance; no model calls"
	@echo "make attest-runtime Hash installed REA/Ghidra/harness dependencies offline; no model calls"
	@echo "make check-attestation Verify runtime drift, provider freezing and secret exclusion offline"
	@echo "make setup-grading Capture and validate pinned C/C++/Rust/Go grading runtime images offline"
	@echo "make check-grading-runtime Verify image integrity, dependency isolation and candidate filesystem limits offline"
	@echo "make setup-c-executor Prepare the pinned freestanding C emulator; no model calls"
	@echo "make check-c-executor Verify host-owned returns, memory effects, ABI checks and guest fault controls offline"
	@echo "make setup-capabilities Pin local original-target compiler/binutils images; no model calls"
	@echo "make build-capabilities Build six reusable capability families from configuration; reuse valid action caches"
	@echo "make check-capabilities Check typed artifact/localization preparation offline; model tasks stay disabled"
	@echo "make prepare-capabilities Freeze public task packages and private grading specs; no model calls"
	@echo "make check-structured-grading Verify closed JSON artifact workers and declared cache dependencies offline"
	@echo "make check-buffer-capabilities Verify C1–C4 against stripped originals with authenticated status and memory effects"
	@echo "make check-lifecycle-capabilities Verify C5/C6 callbacks, lifecycle and interleaved state offline"
	@echo "make check-cpp-lifecycle Verify matched C++20 lifecycle recovery against the same originals"
	@echo "make check-abi-probes Verify T1/T2/T5 descriptions against original compiled callers/callees in both directions"
	@echo "make check-cpp-buffer Verify original-language C++ pairing against the same C buffer targets and vectors"
	@echo "make check-capability-coordinator TEST_JOBS=4  Verify typed capture/cache reuse in isolated workers; no model calls"
	@echo "make check-requests Verify final provider caps, retries and installed-client loopback transports offline"
	@echo "make check-pointer-probes Verify T3 pointer/object descriptions against both frozen original endpoints offline"
	@echo "make check-variant-objects Verify T4 tagged variants and subobjects against frozen original endpoints offline"
	@echo "make check-indirect-calls Verify T6 function pointers, receiver relationships, virtual slots and thunks offline"
	@echo "make build-optimized-locations Build real L6 optimized/LTO examples with separate pinned tooling; no model calls"
	@echo "make check-measurement  Offline return/import/usage/cache controls; no model calls"
	@echo "make regrade        Versioned offline grades/accounting and global reports; original inference and grades retained"
	@echo "make check          Warm integrity verification of prepared cases and accepted saved caches; no builds or model calls"
	@echo "make check-warm     Same warm path, with stage timings and a 180-second budget"
	@echo "make check-cold     Full regression suite, including cold-cache builds, graders and client transports"
	@echo "make check-runner   Verify cache reuse and missing-cache dispatch using offline fixtures"
	@echo "make check-reports  Rebuild twice and compare output hashes and local links"
	@echo "make check-environment Replay cached evidence offline through both adapters; no model calls"
	@echo "make audit-environment Audit cached DSH sessions offline; no model calls"
	@echo "make audit-condition Audit saved fixed-harness inputs; no model calls"
	@echo "make build-cpp-points Validate GCC/Clang compiler, optimization and language-feature builds; no model calls"
	@echo "make archive-profiles Preserve the exact source/configuration versions of saved attempts; no model calls"
	@echo "make clean-reports  Remove generated report output only"
	@echo "make list           Show registered suites, models and saved experiment status"

# Offline tool image lifecycle: no benchmark/model calls.
.PHONY: runtime-setup runtime-smoke runtime-prune runtime-shell check-runtime-image
RUNTIME_FLAGS ?=
runtime-setup:
	$(PYTHON) benchmarks/scripts/runtime_image.py setup $(RUNTIME_FLAGS)
runtime-smoke:
	$(PYTHON) benchmarks/scripts/runtime_image.py smoke $(RUNTIME_FLAGS)
runtime-prune:
	$(PYTHON) benchmarks/scripts/runtime_image.py prune
runtime-shell:
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- /usr/bin/bash --noprofile --norc
check-runtime-image:
	$(PYTHON) benchmarks/scripts/check_runtime_image.py

.PHONY: check-runtime-reuse profile-runtime-reuse
.PHONY: check-preparation-reuse profile-preparation-reuse
check-preparation-reuse:
	cd benchmarks/scripts && $(PYTHON) -m unittest check_preparation_session check_read_session check_deferred_selection check_runtime_reuse.StartupOnce

profile-preparation-reuse:
	$(PYTHON) benchmarks/scripts/profile_preparation_reuse.py

.PHONY: profile-action-resolution
PROFILE_MODE ?= resolve
profile-action-resolution:
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- $(PYTHON) benchmarks/scripts/profile_action_resolution.py --mode $(PROFILE_MODE)

check-runtime-reuse:
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m unittest check_runtime_reuse.StartupOnce check_runtime_reuse.ResponseLimit -v
profile-runtime-reuse:
	$(PYTHON) benchmarks/scripts/runtime_storage.py run -- $(PYTHON) benchmarks/scripts/measure_runtime_reuse.py

.PHONY: check-adaptive-budget
check-adaptive-budget:
	PYTHONPATH=benchmarks/scripts $(PYTHON) -m unittest check_adaptive_budget
