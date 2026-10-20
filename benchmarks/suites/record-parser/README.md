# Basic native record parser

The original C++20, Rust 2021 and Go fixtures now live together in this suite.
Authoring sources, public binaries/prompts/manifests and hidden grading vectors
are unchanged. They cover validation order, little-endian reads, byte records,
wrapping arithmetic, loops and tag dispatch.

Earlier C-output and original-language results remain under their original
experiment directories in benchmarks/runs. ZCode/GLM uses these same native
tasks through the shared coordinator rather than defining another benchmark.

Run make reports at the workspace root to generate all comparisons. See
[the benchmark guide](../../README.md) for layout, offline checks and execution
controls. Experiments remain stopped.
