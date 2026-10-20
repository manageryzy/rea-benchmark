# Third-party notices

The root WTFPL license applies to original benchmark code and reference program
sources. It does not replace licenses for third-party code or linked runtimes.

Frozen ELF inputs are built from the reference programs, with compiler and build
flags recorded in their manifests. Their standard libraries may include
third-party code. The following notices are copied unchanged from the installed
toolchains used by this workspace:

- Go 1.23.2: [BSD license and copyright notice](licenses/go/LICENSE).
- Rust 1.91.1 standard library: [copyright and dependency notices](licenses/rust/COPYRIGHT-library.html),
  with the accompanying license texts in `licenses/rust/licenses/`.
- GCC 13 and the installed GCC 14 runtime packages: [GCC 13 notices](licenses/gcc13/copyright)
  and [GCC 14 notices](licenses/gcc14/copyright), including the GCC Runtime Library Exception.

These files preserve the upstream notices; their inclusion does not assert that
every listed component is linked into every fixture. Other system libraries are
resolved on the host and are not bundled in this repository.

REA, Ghidra, Java, Node.js, DeepSeek Harness, ZCode, compilers and npm dependencies
are installed separately and retain their upstream licenses. Downloaded packages
and the `upstream/` reference checkout are excluded from Git. Versions and sources
are recorded in `environment-lock.json` and `package-lock.json`.
