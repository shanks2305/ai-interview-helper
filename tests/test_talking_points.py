from __future__ import annotations

import unittest

from ai_interview.talking_points import extract_talking_points


class TalkingPointsTests(unittest.TestCase):
    def test_prose_lifts_concrete_example(self) -> None:
        text = (
            "A mutex is a lock so only one thread mutates shared memory at a time. "
            "Use it for in-process state, not for work across services. "
            "For example the checkout API takes a mutex around the inventory map, then writes the order to Postgres. "
            "Do not hold it across network I/O or you will stall the request thread. "
            "A queue is a better fit once the work leaves the process."
        )
        points = extract_talking_points(text)
        self.assertGreaterEqual(len(points.bullets), 3)
        self.assertLessEqual(len(points.bullets), 5)
        self.assertIn("Postgres", points.example)
        self.assertTrue(all("Postgres" not in item for item in points.bullets))
        self.assertIn("Example:", points.as_text())

    def test_star_uses_action_as_example(self) -> None:
        text = """
        **Situation:** Checkout p99 was 2s during a sale.
        **Task:** I owned the payments API.
        **Action:** I put Stripe webhooks on a Redis queue in front of the worker.
        **Result:** p99 dropped to 400ms and error rate fell by half.
        """
        points = extract_talking_points(text)
        self.assertEqual(len(points.bullets), 3)
        self.assertTrue(any(item.startswith("Situation:") for item in points.bullets))
        self.assertIn("Redis", points.example)
        self.assertIn("Action:", points.example)

    def test_markdown_bullets_are_used(self) -> None:
        text = """
        - Lead with a cache in front of the catalog DB
        - Keep the source of truth on Postgres
        - Invalidate on write through a Kafka topic
        - Fail open to stale reads for 30 seconds
        """
        points = extract_talking_points(text)
        self.assertEqual(len(points.bullets), 3)
        self.assertIn("Kafka", points.example or " ".join(points.bullets))

    def test_code_fences_are_stripped(self) -> None:
        text = (
            "Two-pointer scan from both ends until they meet.\n"
            "```python\ndef foo():\n    return 1\n```\n"
            "That keeps time at O(n) and extra space at O(1)."
        )
        points = extract_talking_points(text)
        self.assertTrue(points.bullets)
        self.assertNotIn("def foo", points.as_text())

    def test_empty_and_short(self) -> None:
        self.assertEqual(extract_talking_points("").bullets, [])
        points = extract_talking_points("A mutex is a lock.")
        self.assertEqual(points.bullets, ["A mutex is a lock."])
        self.assertEqual(points.example, "")
