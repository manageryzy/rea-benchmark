"""Regression checks for long answers and code emitted before the closing tool."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE.parents[1] / "scripts"))
from collect_answer import collect


class CaptureTests(unittest.TestCase):
    def capture(self, events, committed=None):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "events.jsonl").write_text("".join(json.dumps(event) + "\n" for event in events))
            if committed is not None:
                (directory / "assistant-messages.jsonl").write_text("".join(json.dumps({"text": text}) + "\n" for text in committed))
            return collect(directory)

    def test_complete_final_replaces_clipped_prefix(self):
        full = "```cpp\n" + "// readable source\n" * 700 + "```"
        events = [{"type": "text", "text": full[:8192], "truncated": True}, {"type": "final", "text": full}]
        self.assertEqual(self.capture(events), full)

    def test_earlier_code_survives_final_acknowledgment(self):
        code = "```rust\npub fn sample() {}\n```"
        events = [{"type": "text", "text": code}, {"type": "status", "phase": "step_end"},
                  {"type": "status", "phase": "step_start"}, {"type": "text", "text": "Closed."},
                  {"type": "final", "text": "Closed."}]
        self.assertEqual(self.capture(events), code + "\n\nClosed.")

    def test_lossless_intermediate_exceeds_projection_limit(self):
        code = "```go\n" + "// original bytes\n" * 800 + "```"
        events = [{"type": "text", "text": code[:8192], "truncated": True},
                  {"type": "status", "phase": "step_end"}, {"type": "text", "text": "Closed."},
                  {"type": "final", "text": "Closed."}]
        self.assertEqual(self.capture(events, [code, "Closed."]), code + "\n\nClosed.")

    def test_chunks_join_without_invented_newlines(self):
        events = [{"type": "text", "text": "```cpp\nret"}, {"type": "text", "text": "urn 1;\n```"},
                  {"type": "thinking", "text": "private reasoning"}, {"type": "final", "text": "```cpp\nreturn 1;\n```"}]
        self.assertEqual(self.capture(events), "```cpp\nreturn 1;\n```")


if __name__ == "__main__":
    unittest.main()
