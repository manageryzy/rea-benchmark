# Rootless pinned runtime image

The benchmark runs its tools inside a Bubblewrap user/mount/PID namespace. It
shares the host kernel and network; it does not boot systemd, need a Docker daemon,
or require root to build or run. Model credentials, account state, results and
source checkouts are never included in the image. This is a reproducible tool
environment, not a replacement for the candidate grading sandbox.

## Dependencies

On Ubuntu/WSL, install once from your normal terminal:

```sh
sudo apt update
sudo apt install -y bubblewrap squashfs-tools squashfuse fuse3 zstd
```

FUSE additionally needs `/dev/fuse` visible and readable/writable by your user.
If it is absent in WSL, `sudo modprobe fuse` may expose it. A restricted application
sandbox can still hide the device even when the ordinary WSL terminal sees it.
The runner automatically falls back in that case. Do not run benchmarks as root.

Install the configured compiler versions before capturing: GCC 13, Clang 20,
Rust 1.91.1, the workspace Go 1.23.2, Java 21 and Python 3.12. Existing setup and
grading toolchain installation instructions remain in [setup.md](setup.md).

## Standard entrypoints

```sh
make runtime-setup       # Build once; reuse the pinned image on subsequent calls
make runtime-smoke       # Offline compile/run in GCC, Clang, Rust, Go; Java/Node checks
make runtime-smoke RUNTIME_FLAGS=--backend=extract  # Exercise the fallback explicitly
make runtime-prune       # Only remove the exact superseded runtime tar after smoke passes
make runtime-shell       # Interactive shell inside the same rootless environment
make benchmark           # Normal configured run, now using the pinned image
```

`make runtime-smoke` makes no model requests. The last command can make model
requests under the normal configured budgets and admission gates.

Use `make runtime-setup RUNTIME_FLAGS=--refresh` to explicitly select newly
installed tool bytes. Add `--reuse-runtime` to refresh system tools while retaining
the selected public harness archive (and reusing its previous image contents). Existing grading archives, action caches, result attempts
and receipts are retained. A new tool image does not certify old validation
receipts and does not force-run the 104-check suite. Relevant changed dependencies
remain subject to normal admission; old results are not relabeled.

## Contents and configuration

`benchmarks/config/runtime-image.json` declares tools, system directories, image
compression, backend, and Linux cache location. `runtime-storage.json` schema 2
selects this path; mode `off` explicitly retains host execution.

The image contains the pinned REA/DSH/ZCode/Node/Ghidra directories, workspace Go
and formatting tools, plus captured GCC/Clang compiler executables, headers,
linker/runtime objects, Rust compiler and sysroot, Java, Python, shell/build
utilities, zstd for historical durable-session auditing, and their resolved ELF libraries. A content manifest records compiler
versions and native file digests. Capture inspects only trusted installed tools.

The image binds tools read-only at their normal paths. Shared host facilities
remain outside it: kernel, networking, resolver settings and other uncaptured
`/etc` operational configuration, workspace, account state and credentials. The
declared CA bundle and loader cache are pinned read-only with the image. It is
not a complete hermetic OS snapshot or an OCI image. Independent grading continues
to use its existing closed compiler/executor images and records.

## Mount and cache lifecycle

1. Capture system tools and run the offline compiler smoke with networking disabled.
   Capture the public runtime once, build a SquashFS image, hash it,
   then atomically publish a content-addressed image and manifest.
2. Keep the image under `~/.cache/rea/runtime-images/<workspace-key>` on Linux
   storage, outside the Windows mount. A lease lock excludes capture/prune while
   running; a workspace lock excludes duplicate benchmark starts.
3. With FUSE available, mount using `squashfuse` and bind that mount into Bubblewrap.
   A failed mount in `auto` mode falls back to extraction. A task failure never
   triggers a second execution via fallback.
4. Without FUSE, use `unsquashfs` once and atomically publish a reusable tree.
   Subsequent runs reuse it; only scratch files are recreated. `backend: extract`
   forces this path; `backend: fuse` requires FUSE and fails if unavailable.
5. On success, failure or handled interruption, stop the task before unmounting
   FUSE and deleting per-run scratch. Images and fallback trees are persistent
   caches, not temporary task files. Stale unlocked run directories are reaped.

Image hash verification is memoized by device/inode/mode/size/mtime/ctime, not
atime. A changed tuple requires rehashing. The fallback cache is private to its
owner and mounted read-only for tasks, but is not protected from a malicious
same-user host process. Existing before/after runtime attestation is retained;
this optimization is not permission to skip it. Full traces include image lookup,
hash verification, fallback extraction, container launch and cleanup. The
bootstrap Python/Bubblewrap/FUSE helpers run on the host before entering the image.

## Complete dependency admission before publication

Image capture includes the absolute system paths declared in
`benchmarks/config/runtime-attestation.json`, in addition to compiler requirements
from `runtime-image.json`. The required roots are restricted to reviewed public
system locations; account homes and credentials are not packaging inputs.
Declared system configuration files such as the loader cache and CA bundle are
mounted read-only from the image. The native loader dependencies of pinned
Node/Ghidra/Harness files are resolved and added as well.

Before `active.json` selects a candidate image, the builder runs network-isolated
acceptance: all declared paths, a zstd session compression/decompression roundtrip,
REA/DSH/ZCode CLI startup, actual installed DSH and
ZCode request serialization against offline fixture responses, Ghidra analysis of the
trusted `/usr/bin/true` fixture, REA MCP open/query/close of that fixture, and real `runtime_attestation.capture` for both
Harnesses with native loader validation. CLI help invocations use a temporary
empty home and no account credentials. No model prompts are submitted and no
coordinator test suite is run. Failures leave the old active image selected.
The image manifest retains the acceptance results and source/policy digests.
Offline request checks assert zero provider forwards; they are separate from
real model runs and do not need historical benchmark results.
The `/lib64` dynamic loader aliases retain their canonical `/usr/lib` targets;
flattening these links changes runtime identity and is rejected by admission.

Refresh an existing image while reusing pinned public tool contents with:

```sh
make runtime-setup RUNTIME_FLAGS='--refresh --reuse-runtime'
```

If report finalization failed because a historical `session.v4.jsonl.zstd` could
not be decoded, install zstd on the host and refresh with the command above. The
image admission must pass its real codec roundtrip before the new image becomes
active. Inside `make runtime-shell`, run `make audit-condition` followed by
`make reports`. These entrypoints read saved attempts; neither launches models nor
reruns the benchmark validation suite. Keep the original failed command receipt:
an offline report repair does not change its historical exit status or grades.
The audit hashes and decodes the same compressed bytes and rejects corrupt,
truncated or mismatched sessions instead of certifying them.

## Cleanup and historical evidence

`runtime-prune` requires a successful smoke receipt for the selected image. It
hash-checks and removes only the matching old tar in
`.cache/benchmark-runtime/installed-runtime/`, records the replacement image and
retains its metadata. It never removes grading archives, provider traces,
benchmark action CAS, results, or unrelated installer packages.

After pruning, the selected SquashFS image is the retained copy of those pinned
runtime bytes. Keep that cache if historical runtime reproduction is needed.

## Measured startup (2026-10-25, this WSL host)

The completed image passed C++ (GCC and Clang), Rust and Go compile/run tests,
Java/Node startup, read-only mount checks and nested Bubblewrap, under both
FUSE and the extraction backend. These are offline controls, not model scores.

| Environment operation | Elapsed |
| --- | ---: |
| FUSE warm start, execute `true`, unmount and clean scratch | 0.245 s |
| First fallback extraction and `true` | 15.233 s |
| Reuse fallback tree, execute `true`, clean scratch | 0.198 s |

These times include profiling overhead inside the process and exclude initial
Python imports, one-time image capture, subsequent benchmark admission/attestation,
grading and inference. Private traces and the timing ledger are under
`.cache/benchmark-runtime/profiling/rootless-*`; no model requests were made.
