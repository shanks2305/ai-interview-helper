from __future__ import annotations

import unittest
from unittest.mock import patch

from ai_interview.modes import (
    DEFAULT_ANSWER_MODE,
    SYSTEM_PROMPT,
    TYPED_PROMPT,
    answer_mode_catalog,
    max_tokens_for_mode,
    normalize_answer_mode,
    normalize_redraft_instruction,
    prompt_for_mode,
    redraft_mode_override,
    uses_markdown,
)


class AnswerModeTests(unittest.TestCase):
    def test_aliases_map_to_real_modes(self) -> None:
        self.assertEqual(normalize_answer_mode(None), DEFAULT_ANSWER_MODE)
        self.assertEqual(normalize_answer_mode(""), "auto")
        self.assertEqual(normalize_answer_mode("15s"), "spoken_20")
        self.assertEqual(normalize_answer_mode("STAR"), "star")
        self.assertEqual(normalize_answer_mode("system-design"), "system_design")
        self.assertEqual(normalize_answer_mode("glanceable"), "glanceable")
        self.assertEqual(normalize_answer_mode("bullets"), "glanceable")
        self.assertEqual(normalize_answer_mode("nope"), "auto")

    def test_catalog_lists_distinct_modes(self) -> None:
        catalog = answer_mode_catalog()
        self.assertEqual(
            [item["id"] for item in catalog],
            ["auto", "spoken_20", "spoken_45", "glanceable", "star", "system_design", "coding"],
        )
        self.assertTrue(catalog[0]["routes"])
        self.assertTrue(uses_markdown("star"))
        self.assertFalse(uses_markdown("spoken_20"))

    def test_prompts_differ_by_mode(self) -> None:
        self.assertIn("**definition:**", prompt_for_mode("spoken_45").lower())
        self.assertIn("15–20 second", prompt_for_mode("spoken_20"))
        self.assertIn("STAR", prompt_for_mode("star"))
        self.assertIn("**Goal:**", prompt_for_mode("system_design"))
        self.assertIn("runnable code", prompt_for_mode("coding"))
        self.assertIn("glanceable", prompt_for_mode("glanceable").lower())
        self.assertIn("pasted as text", prompt_for_mode("spoken_45", source="typed").lower())
        self.assertIn("never claim the candidate used a technology", " ".join(SYSTEM_PROMPT.split()).lower())
        self.assertNotEqual(SYSTEM_PROMPT, TYPED_PROMPT)

    def test_token_caps(self) -> None:
        with patch("ai_interview.modes.LLM_MAX_TOKENS", 320):
            self.assertEqual(max_tokens_for_mode("spoken_20"), 500)
            self.assertEqual(max_tokens_for_mode("spoken_45"), 1400)
            self.assertEqual(max_tokens_for_mode("star"), 900)
            self.assertEqual(max_tokens_for_mode("coding", source="typed"), 2048)
        with patch("ai_interview.modes.LLM_MAX_TOKENS", None):
            self.assertEqual(max_tokens_for_mode("glanceable"), 700)
        with patch("ai_interview.modes.LLM_MAX_TOKENS", 3000):
            self.assertEqual(max_tokens_for_mode("spoken_45"), 3000)

    def test_redraft_instruction_aliases(self) -> None:
        self.assertEqual(normalize_redraft_instruction("short"), "shorter")
        self.assertEqual(normalize_redraft_instruction("example"), "concrete")
        self.assertEqual(redraft_mode_override("shorter"), "spoken_20")
        self.assertEqual(redraft_mode_override("star"), "star")
        self.assertIsNone(redraft_mode_override("concrete"))
        self.assertEqual(redraft_mode_override("again", "coding"), "coding")
