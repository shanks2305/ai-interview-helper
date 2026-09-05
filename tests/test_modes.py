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
    prompt_for_mode,
    uses_markdown,
)


class AnswerModeTests(unittest.TestCase):
    def test_default_and_aliases(self) -> None:
        self.assertEqual(normalize_answer_mode(None), DEFAULT_ANSWER_MODE)
        self.assertEqual(normalize_answer_mode(""), "spoken_45")
        self.assertEqual(normalize_answer_mode("15s"), "spoken_15")
        self.assertEqual(normalize_answer_mode("STAR"), "star")
        self.assertEqual(normalize_answer_mode("system-design"), "system_design")
        self.assertEqual(normalize_answer_mode("glanceable"), "bullets")
        self.assertEqual(normalize_answer_mode("nope"), "spoken_45")

    def test_catalog_order_and_markdown_flags(self) -> None:
        catalog = answer_mode_catalog()
        self.assertEqual(
            [item["id"] for item in catalog],
            ["spoken_15", "spoken_45", "star", "system_design", "bullets"],
        )
        self.assertFalse(uses_markdown("spoken_15"))
        self.assertFalse(uses_markdown("45s"))
        self.assertTrue(uses_markdown("star"))
        self.assertTrue(uses_markdown("design"))
        self.assertTrue(uses_markdown("bullets"))

    def test_spoken_prompts_forbid_markdown(self) -> None:
        spoken_15 = prompt_for_mode("spoken_15")
        spoken_45 = prompt_for_mode("spoken_45")
        self.assertIn("15-second", spoken_15)
        self.assertIn("no markdown", spoken_15.lower())
        self.assertEqual(spoken_45, SYSTEM_PROMPT)
        self.assertIn("no markdown", spoken_45.lower())
        self.assertIn("45 seconds", spoken_45)

    def test_typed_keeps_coding_prompt_for_45s(self) -> None:
        self.assertEqual(prompt_for_mode("spoken_45", source="typed"), TYPED_PROMPT)
        self.assertIn("15-second", prompt_for_mode("spoken_15", source="typed"))
        self.assertIn("Situation:", prompt_for_mode("star", source="typed"))
        self.assertIn("Requirements", prompt_for_mode("system_design"))
        self.assertIn("3-5 markdown bullets", prompt_for_mode("bullets"))

    def test_token_caps(self) -> None:
        with patch("ai_interview.modes.LLM_MAX_TOKENS", 320):
            self.assertEqual(max_tokens_for_mode("spoken_15"), 160)
            self.assertEqual(max_tokens_for_mode("spoken_45"), 320)
            self.assertEqual(max_tokens_for_mode("spoken_45", source="typed"), 2048)
            self.assertEqual(max_tokens_for_mode("star"), 700)
            self.assertEqual(max_tokens_for_mode("system_design"), 1200)
            self.assertEqual(max_tokens_for_mode("bullets"), 400)
        with patch("ai_interview.modes.LLM_MAX_TOKENS", None):
            self.assertIsNone(max_tokens_for_mode("spoken_45"))
            self.assertEqual(max_tokens_for_mode("spoken_45", source="typed"), 2048)
            self.assertEqual(max_tokens_for_mode("star"), 700)
