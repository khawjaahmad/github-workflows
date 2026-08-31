"""Transcript compaction."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qa_agent import history  # noqa: E402


def conversation(turns, output_chars=1000):
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "the diff"},
    ]
    for index in range(turns):
        call_id = "call_%d" % index
        messages.append(
            {
                "role": "assistant",
                "content": "thinking about it " * 20,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "bash", "arguments": '{"command": "true"}'},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": call_id, "content": "o" * output_chars})
    return messages


class SizeTest(unittest.TestCase):
    def test_counts_content_and_tool_call_arguments(self):
        messages = [
            {
                "role": "assistant",
                "content": "abc",
                "tool_calls": [{"function": {"name": "bash", "arguments": '{"command": "ls"}'}}],
            },
        ]
        self.assertEqual(history.size(messages), 3 + 4 + len('{"command": "ls"}'))

    def test_tolerates_missing_content(self):
        self.assertEqual(history.size([{"role": "assistant"}]), 0)


class CompactTest(unittest.TestCase):
    def test_untouched_when_it_already_fits(self):
        messages = conversation(3)
        self.assertIs(history.compact(messages, 1000000), messages)

    def test_disabled_by_a_zero_budget(self):
        messages = conversation(20)
        self.assertIs(history.compact(messages, 0), messages)

    def test_shrinks_to_fit(self):
        messages = conversation(20)
        before = history.size(messages)

        compacted = history.compact(messages, 8000)

        self.assertLessEqual(history.size(compacted), 8000)
        self.assertLess(history.size(compacted), before)

    def test_never_drops_a_message_or_a_tool_call(self):
        messages = conversation(20)

        compacted = history.compact(messages, 5000)

        self.assertEqual(len(compacted), len(messages))
        called = [c["id"] for m in compacted for c in m.get("tool_calls") or []]
        replied = [m["tool_call_id"] for m in compacted if m["role"] == "tool"]
        self.assertEqual(called, replied)

    def test_recent_turns_are_preserved_while_the_budget_allows(self):
        messages = conversation(20)

        compacted = history.compact(messages, 20000, keep_recent=6)

        for original, kept in zip(messages[-6:], compacted[-6:]):
            self.assertEqual(original["content"], kept["content"])

    def test_an_oversized_result_in_the_last_exchange_is_truncated(self):
        messages = conversation(1, output_chars=30000)

        compacted = history.compact(messages, 5000)

        self.assertLessEqual(history.size(compacted), 5000)
        self.assertEqual(len(compacted), len(messages))
        result = compacted[-1]["content"]
        self.assertIn("truncated to fit the context window", result)
        self.assertTrue(messages[-1]["content"].startswith(result.split("\n[...")[0]))
        self.assertGreaterEqual(len(result.split("\n[...")[0]), history.MIN_TOOL_CHARS)

    def test_a_tiny_budget_leaves_a_small_bounded_residue(self):
        messages = conversation(20)
        before = history.size(messages)

        compacted = history.compact(messages, 100)

        self.assertEqual(len(compacted), len(messages))
        self.assertLess(history.size(compacted), before // 4)

    def test_truncation_never_edits_a_reasoning_block(self):
        messages = conversation(1, output_chars=30000)
        messages[2]["reasoning_content"] = "r" * 8000

        compacted = history.compact(messages, 5000)

        kept = compacted[2].get("reasoning_content")
        self.assertTrue(kept is None or kept == messages[2]["reasoning_content"])

    def test_the_oldest_output_goes_first(self):
        messages = conversation(20)

        compacted = history.compact(messages, 20000)

        tools_ = [m for m in compacted if m["role"] == "tool"]
        self.assertTrue(tools_[0]["content"].startswith("[earlier output elided"))
        self.assertFalse(tools_[-1]["content"].startswith("[earlier output elided"))

    def test_the_original_is_not_mutated(self):
        messages = conversation(20)

        history.compact(messages, 5000)

        self.assertTrue(all(m["content"] and "elided" not in m["content"] for m in messages))

    def test_assistant_prose_goes_after_tool_output(self):
        messages = conversation(30, output_chars=50)

        compacted = history.compact(messages, 1500)

        elided = [m for m in compacted if (m["content"] or "").startswith("[earlier output")]
        self.assertTrue(any(m["role"] == "assistant" for m in elided))

    def test_best_effort_when_nothing_more_can_be_shrunk(self):
        messages = [{"role": "user", "content": "d" * 5000}]
        self.assertEqual(history.compact(messages, 100), messages)


if __name__ == "__main__":
    unittest.main()
