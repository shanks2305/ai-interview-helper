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
    def test_legacy_styles_collapse_to_one_answer(self) -> None:
        self.assertEqual(normalize_answer_mode(None), DEFAULT_ANSWER_MODE)
        self.assertEqual(normalize_answer_mode(""), "spoken_45")
        self.assertEqual(normalize_answer_mode("15s"), "spoken_45")
        self.assertEqual(normalize_answer_mode("STAR"), "spoken_45")
        self.assertEqual(normalize_answer_mode("system-design"), "spoken_45")
        self.assertEqual(normalize_answer_mode("glanceable"), "spoken_45")
        self.assertEqual(normalize_answer_mode("nope"), "spoken_45")

    def test_catalog_is_a_single_answer(self) -> None:
        catalog = answer_mode_catalog()
        self.assertEqual([item["id"] for item in catalog], ["spoken_45"])
        self.assertTrue(uses_markdown("spoken_45"))
        self.assertTrue(uses_markdown("star"))

    def test_spoken_and_typed_prompts(self) -> None:
        self.assertEqual(prompt_for_mode("spoken_45"), SYSTEM_PROMPT)
        self.assertEqual(prompt_for_mode("star", source="spoken"), SYSTEM_PROMPT)
        self.assertEqual(prompt_for_mode("spoken_45", source="typed"), TYPED_PROMPT)
        self.assertEqual(SYSTEM_PROMPT, TYPED_PROMPT)
        self.assertIn("**definition:**", SYSTEM_PROMPT.lower())
        self.assertIn("**explanation:**", SYSTEM_PROMPT.lower())
        self.assertIn("**example:**", SYSTEM_PROMPT.lower())
        self.assertIn("code", SYSTEM_PROMPT.lower())
        self.assertIn("never claim the candidate used a technology", " ".join(SYSTEM_PROMPT.split()).lower())

    def test_token_caps(self) -> None:
        with patch("ai_interview.modes.LLM_MAX_TOKENS", 320):
            self.assertEqual(max_tokens_for_mode("spoken_45"), 1400)
            self.assertEqual(max_tokens_for_mode("spoken_45", source="typed"), 2048)
            self.assertEqual(max_tokens_for_mode("star"), 1400)
        with patch("ai_interview.modes.LLM_MAX_TOKENS", None):
            self.assertEqual(max_tokens_for_mode("spoken_45"), 1400)
            self.assertEqual(max_tokens_for_mode("spoken_45", source="typed"), 2048)
        with patch("ai_interview.modes.LLM_MAX_TOKENS", 3000):
            self.assertEqual(max_tokens_for_mode("spoken_45"), 3000)
