# Local binary-analysis environment

Use `/mnt/h/work/rea/bin/rea` for this workspace's REA CLI. It supplies the
pinned Node runtime, Ghidra installation, and Java environment. The default
provider is `ghidra`. Use the `rea` MCP server when it is connected.

Start DeepSeek Harness with `/mnt/h/work/rea/bin/dsh web --no-open`. It has
workspace-local configuration and workflow instructions. Read `guides/setup.md` for
paths, verification commands, and client registration.

Prefer focused function, assembly, string, and xref queries. Preserve Evidence
and distinguish decompiler observations from inferred source behavior. Close
binary sessions after completing an investigation. When running the model
comparison exercise, follow its restrictions on reading source and prior output.

The repository in `upstream/rea` is an unmodified research reference. Its own
AGENTS.md applies to changes within that checkout. Tools, target binaries,
sessions, credentials, and analysis output are local artifacts.
