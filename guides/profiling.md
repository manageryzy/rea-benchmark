# Benchmark profiling

The normal benchmark now emits a private Chrome Trace automatically. To choose
its filename explicitly:

```sh
make benchmark TRACE=.cache/benchmark-runtime/profile-unique.trace.json
```

Use a new output name for each run. Existing traces are never overwritten.
The traced entrypoint stages the pinned runtime before selection, so it avoids
the untraced entrypoint's outer selection followed by a second selection in WSL.
Tracing itself starts **before** runtime staging. `make benchmark` enters
`benchmark_trace.py --staged` first, then calls the unchanged runtime-storage
implementation. It covers WSL detection, staging/package locks, package lookup,
archive reads, SHA256 updates, Linux snapshot writes, tar extraction, the
post-extraction checksum, child-process waiting and temporary-directory cleanup.
Tar iteration has a whole-phase span; individual member and IO calls below
20 ms contribute to summary counters without generating individual events.
No archive member names, paths, contents or credentials are recorded.

The benchmark worker uses the parent's monotonic-clock epoch on a separate
process lane. After termination, worker events are merged into the requested
trace filename. The `.runtime.json` and `.worker.json` originals remain alongside
it when both exist. If staging is interrupted before the worker starts, the
requested trace already contains the staging evidence. SIGINT/SIGTERM use the
existing cleanup guard and finish the JSON; a forcibly killed worker's complete
events are retained without inventing missing end events. The normal entrypoint
does not render the large HTML viewer automatically; open JSON in Perfetto.
All normal admission gates, action identities, budget settings and saved-attempt
rules still apply. Tracing does not authorize a model call on its own.

`benchmark_trace.traced` is a `functools.wraps` decorator. The tracing entrypoint
installs it temporarily on critical module functions and class methods, restoring
them afterwards. This leaves validated implementation files byte-identical.
It covers task selection, coordinator gates, action construction/lookups,
task preparation, runtime attestations, provider dispatch, grading and reports.
No arguments, local variables, prompts, model responses or credentials are recorded.
Trace files are local diagnostic artifacts, excluded from the public report.
Hot file/CAS operations contribute to all-call counters; only calls lasting at
least 20 ms emit individual spans, to keep full-campaign traces manageable.

The entrypoint also scopes `read_session.ReadSession` to this process. Successful
read-only admissions can be reused after checking device/inode/mode/size/mtime/
ctime, resolved symlink targets, parent identities and directory enumerations.
Missing-file creation, changed loaded helpers, context-variable controls and
environment changes invalidate reuse. Failures and functions that write files
are not retained. Returned data is copied to prevent caller mutation. This is a
metadata cache, not protection against a privileged actor forging filesystem
metadata. Source hashes, AC/CAS verification and action-key computation remain
the original functions; no old validation receipt is rewritten. Actual runtime
capture and pre/post model verification are never memoized by this layer.

The process-local cache includes preparation actions, frozen tasks, private
grading specifications, toolchain manifest admission and shared original-binding
checks. A successful child admission is reusable both inside `inspect` and when
called directly. Its complete read set is merged into its parent even on a hit;
changed child files, failed checks and nested writes cannot disappear from the
parent's dependencies. Cache capacity is 2048 entries. Each new process starts
empty: this does not replace persistent AC/CAS evidence with unverified passes.
Unchanged inputs reuse their validated result rather than rereading/rehashing
content. Hit checks use mtime **and ctime**, size, device/inode/mode and parent
identities; atime is deliberately excluded because reads may update it.

Temporary `metadata_validation_cache` scope wrappers are compared by their
captured validator implementation, so moving from selection to resolution does
not discard an otherwise valid admission. Other helper replacements still
invalidate reuse. This changes the profiling/acceleration layer, not the frozen
task/grader code, action keys or coordinator validation receipts.

### Offline preparation microprofile

```sh
make profile-preparation
```

This inspects one existing pointer, native ABI and matched-buffer preparation
three times, across fresh metadata scopes. It reports cold-first `inspect`,
then warm `frozen`/`spec` and subsequent warm repeats separately. Both network
connections and all subprocess launches are forbidden. No models, compilation,
104-check suite or report rebuild are invoked. `PROFILE_FLAGS` can select
`--kinds pointer_probe --repeats 3` and a unique `--output` measurements filename.
`TRACE` can select a unique trace filename; the default is automatic.

The detailed trace adds file-read, path-resolution/lstat, SHA256-constructor,
read-set guard, mirror-manifest and original-binding spans. Leaf calls under
1 ms are counted in the summary without emitting spans. Times are inclusive:
file reads include guards; `load` includes `validate_record`. Do not add those
totals. SHA256 update/digest operations on streaming objects are not separately
instrumented. No file paths or contents enter the trace. This command writes
Chrome/Perfetto JSON and a summary without rendering the large HTML viewer.

Measured on 2026-10-25 in the WSL checkout with detailed tracing enabled:
the pointer, Rust native-ABI and Clang matched-buffer samples had warm `inspect`
70.76–92.13 ms, `frozen` 58.12–87.15 ms and `spec` 40.94–60.42 ms. Initial
per-sample admission still took 3.34–4.28 s. These are bounded preparation
measurements, not an end-to-end campaign timing claim. The reference pointer
probe previously validated the grading manifest 13 times; the final three-case
probe validated two distinct image manifests only twice in total. Original
pointer results had identical output digests. Artifacts are in
`.cache/benchmark-runtime/profiling/preparation-final-20261025.*`; the JSON trace
can be opened directly in Perfetto. The 12 read-session and 5 tracer controls
passed; no coordinator controls or provider calls ran for this change.

For the later resolution/binding read paths, use:

```sh
make profile-preparation PROFILE_FLAGS='--later --repeats 2'
```

This warms the existing preparations, then measures real analysis-context
admission and `registration.scoring_state`, including source-bound gates and
candidate-independent grading inputs. It does not publish a registration, stage
or capture the model runtime, dispatch a provider, or run a candidate. The
session now also reuses guarded Merkle input reads, public-catalogue checks,
grading-input contracts and matched-language admission. Each complete argument
set remains part of the memo key; distinct models/task settings are not silently
coalesced. Current metadata is still checked, and a changed CAS blob (even with
restored mtime and identical size) rejects the hit. Guard workers and the code
root are initialized once per session rather than recreated for each check.

On 2026-10-25 the repeated scoring binding fell from 409/326/6731 ms to
56/51/94 ms for pointer/native-ABI/matched-buffer samples. Admission for the
second and third distinct task fell from 2200/2247 ms to 636/598 ms by reusing
the common proof inputs; repeated admission was 48–54 ms. Initial binding costs
were 2.29/0.83/9.20 seconds (higher than the preceding probe due to populating
nested guarded entries). They are not warm timings. Both probes returned
identical output digests for every measured operation. The 3-case, 2-repeat
probe including preparation took 30.746 s versus 37.170 s before this change.
Artifacts: `.cache/benchmark-runtime/profiling/later-{before,final}-20261025.*`.
The 13 read-session and 5 tracer controls passed; 36 existing coordinator source
hashes remained identical. No 104-check run or provider call was made.

These measurements do not establish complete-batch startup or dispatch timing.
Fresh runtime capture and before/after verification still execute normally;
this cache does not treat a previously captured writable runtime as immutable.

Open the completed `.trace.json` in Chrome tracing or Perfetto. Inclusive-time
summaries are generated beside each stream. The direct profiling CLI can also
generate a self-contained HTML Gantt viewer unless `--no-render` is selected.
The HTML supports zoom, function filtering and duration tooltips.
Nested/inclusive times overlap and must not be added together. Thread lanes show
concurrency; they do not imply CPU utilization. Decorator and trace I/O overhead
is included in the measurement.

To inspect an active trace, or a trace from an interrupted process:

```sh
make trace-view TRACE=.cache/benchmark-runtime/profile-unique.trace.json
```

The viewer marks unmatched starts as unfinished. Never interpret those spans as
completed work. Refreshing the view does not restart the benchmark.

Run the small telemetry-only checks with:

```sh
python3 benchmarks/scripts/check_benchmark_trace.py
python3 benchmarks/scripts/check_read_session.py
```
