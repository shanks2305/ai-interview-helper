from __future__ import annotations

import unittest

from ai_interview.llm import (
    CONTEXT_RESUME_CHARS,
    SYSTEM_PROMPT,
    _history_messages,
    build_system_prompt,
    context_block,
)
from ai_interview.modes import TYPED_PROMPT


class SystemPromptContextTests(unittest.TestCase):
    def test_empty_context_leaves_base_prompt(self) -> None:
        self.assertEqual(build_system_prompt(SYSTEM_PROMPT), SYSTEM_PROMPT)
        self.assertEqual(context_block(), "")

    def test_context_appends_role_company_and_resume(self) -> None:
        prompt = build_system_prompt(
            TYPED_PROMPT,
            role="Staff software engineer",
            company="Acme",
            job_description="Own the payments API.",
            resume="Built Stripe billing at Fintech Co.",
        )
        self.assertTrue(prompt.startswith(TYPED_PROMPT.rstrip()))
        self.assertIn("Staff software engineer", prompt)
        self.assertIn("Acme", prompt)
        self.assertIn("Own the payments API.", prompt)
        self.assertIn("Stripe billing", prompt)
        self.assertIn("Do not recite the resume", prompt)

    def test_resume_is_truncated(self) -> None:
        resume = "x" * (CONTEXT_RESUME_CHARS + 80)
        block = context_block(resume=resume)
        self.assertIn("…", block)
        self.assertLess(len(block), len(resume) + 200)


class HistoryMessageTests(unittest.TestCase):
    def test_history_skips_blank_questions(self) -> None:
        messages = _history_messages([("", "ignored"), ("What is REST?", "An API style.")])
        self.assertEqual(
            messages,
            [
                {"role": "user", "content": "What is REST?"},
                {"role": "assistant", "content": "An API style."},
            ],
        )
