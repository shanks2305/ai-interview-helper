from __future__ import annotations

import unittest

from ai_interview.classify import classify_clip


class ClassifyClipTests(unittest.TestCase):
    def test_skips_filler_and_greetings(self) -> None:
        self.assertEqual(classify_clip("um").reason, "filler")
        self.assertEqual(classify_clip("uh huh yeah").reason, "filler")
        self.assertEqual(classify_clip("hello").reason, "greeting")
        self.assertEqual(classify_clip("Hey, thanks").reason, "greeting")
        self.assertEqual(classify_clip("can you hear me").reason, "greeting")
        self.assertEqual(classify_clip("got it").reason, "greeting")

    def test_skips_false_starts(self) -> None:
        verdict = classify_clip("What is a mutex and")
        self.assertEqual(verdict.action, "skip")
        self.assertEqual(verdict.reason, "incomplete")
        self.assertTrue(verdict.retryable)

    def test_skips_overlap_without_a_question(self) -> None:
        verdict = classify_clip("[crosstalk] yeah")
        self.assertEqual(verdict.action, "skip")
        self.assertEqual(verdict.reason, "overlap")

    def test_extracts_question_from_filler_and_overlap(self) -> None:
        um = classify_clip("um so what is a mutex")
        self.assertEqual(um.action, "answer")
        self.assertIn("mutex", um.text.lower())

        overlap = classify_clip("Yeah I think so. What is the time complexity?")
        self.assertEqual(overlap.action, "answer")
        self.assertIn("time complexity", overlap.text.lower())

    def test_keeps_long_multi_sentence_questions(self) -> None:
        spoken = (
            "Okay. Imagine you have a payments API at about 10k TPS. "
            "Walk me through how you would design idempotency. "
            "What happens if the client retries after a timeout?"
        )
        verdict = classify_clip(spoken)
        self.assertEqual(verdict.action, "answer")
        self.assertIn("10k TPS", verdict.text)
        self.assertIn("idempotency", verdict.text.lower())
        self.assertIn("retries", verdict.text.lower())
        self.assertFalse(verdict.text.lower().startswith("okay"))

    def test_answers_real_interview_questions(self) -> None:
        samples = [
            "What is a REST API?",
            "Tell me about a time you failed",
            "Implement an LRU cache",
            "Design a URL shortener",
            "Big O of binary search?",
            "Walk me through how you would shard a database",
        ]
        for sample in samples:
            with self.subTest(sample=sample):
                verdict = classify_clip(sample)
                self.assertEqual(verdict.action, "answer", verdict)

    def test_follow_ups_are_new_turns(self) -> None:
        answered = ["What is a REST API?"]
        cases = [
            "Can you go deeper on REST APIs?",
            "What about GraphQL?",
            "And how does it compare to GraphQL?",
            "Tell me more",
            "Why?",
            "What if the client retries?",
        ]
        for sample in cases:
            with self.subTest(sample=sample):
                verdict = classify_clip(sample, answered)
                self.assertEqual(verdict.action, "answer", verdict)
                self.assertIn(verdict.reason, {"follow_up", "question"})

    def test_near_duplicate_restatements_are_skipped(self) -> None:
        answered = ["What is a REST API?"]
        cases = [
            "What's a REST API?",
            "What is a REST API",
            "Can you explain what a REST API is?",
            "um what is a rest api",
        ]
        for sample in cases:
            with self.subTest(sample=sample):
                verdict = classify_clip(sample, answered)
                self.assertEqual(verdict.reason, "duplicate", verdict)
                self.assertFalse(verdict.retryable)

    def test_expanded_question_is_not_a_duplicate(self) -> None:
        answered = ["What is a mutex"]
        verdict = classify_clip(
            "What is a mutex and how does it differ from a semaphore",
            answered,
        )
        self.assertEqual(verdict.action, "answer")

    def test_false_start_then_full_question_is_retryable(self) -> None:
        first = classify_clip("What is a mutex and")
        self.assertEqual(first.reason, "incomplete")
        second = classify_clip("What is a mutex and how does it differ from a semaphore")
        self.assertEqual(second.action, "answer")

    def test_typed_paste_skips_only_duplicates(self) -> None:
        hello = classify_clip("hello", typed=True)
        self.assertEqual(hello.action, "answer")
        answered = ["Implement an LRU cache"]
        again = classify_clip("Implement an LRU cache", answered, typed=True)
        self.assertEqual(again.reason, "duplicate")

    def test_different_question_same_topic_is_new(self) -> None:
        answered = ["What is a REST API?"]
        verdict = classify_clip("How would you implement a REST API?", answered)
        self.assertEqual(verdict.action, "answer")

    def test_narrower_follow_up_is_not_a_duplicate(self) -> None:
        answered = ["What is the difference between a process and a thread"]
        verdict = classify_clip("What is a process?", answered)
        self.assertEqual(verdict.action, "answer", verdict)

        gc = classify_clip(
            "Explain garbage collection",
            ["Explain how garbage collection works in Python"],
        )
        self.assertEqual(gc.action, "answer", gc)


class QuestionKindTests(unittest.TestCase):
    def test_routes_coding_design_and_behavioral(self) -> None:
        from ai_interview.classify import classify_question_kind, resolve_turn_mode

        self.assertEqual(classify_question_kind("Implement an LRU cache"), "coding")
        self.assertEqual(classify_question_kind("Design a URL shortener"), "system_design")
        self.assertEqual(classify_question_kind("Tell me about a time you failed"), "behavioral")
        self.assertEqual(classify_question_kind("What is a mutex?"), "technical")
        self.assertEqual(
            classify_question_kind("Example 1:\nInput: [1]\nConstraints:\n1 <= n", typed=True),
            "coding",
        )

        self.assertEqual(resolve_turn_mode("auto", "Implement an LRU cache")[0], "coding")
        self.assertEqual(resolve_turn_mode("auto", "Design a URL shortener")[0], "system_design")
        self.assertEqual(resolve_turn_mode("auto", "Tell me about a time you failed")[0], "star")
        self.assertEqual(resolve_turn_mode("auto", "What is a mutex?")[0], "spoken_45")
        self.assertEqual(resolve_turn_mode("star", "Implement an LRU cache")[0], "star")


if __name__ == "__main__":
    unittest.main()
