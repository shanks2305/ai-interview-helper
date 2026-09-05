from __future__ import annotations

import unittest

from ai_interview.hub import LiveHub


class HubSkipTests(unittest.TestCase):
    def test_skip_does_not_replace_a_real_answer(self) -> None:
        hub = LiveHub()
        hub.apply({"type": "question", "text": "What is a mutex?", "source": "spoken"})
        hub.apply({"type": "answer", "text": "A mutex is a lock.", "source": "spoken"})
        hub.apply(
            {
                "type": "skip",
                "reason": "filler",
                "text": "um",
                "detail": "Skipped · filler",
                "retryable": True,
                "source": "spoken",
            }
        )
        self.assertEqual(hub.question, "What is a mutex?")
        self.assertEqual(hub.answer, "A mutex is a lock.")

    def test_skip_fills_empty_stage(self) -> None:
        hub = LiveHub()
        hub.apply(
            {
                "type": "skip",
                "reason": "greeting",
                "text": "hello",
                "detail": "Skipped · not a question",
                "retryable": True,
                "source": "spoken",
            }
        )
        self.assertEqual(hub.question, "hello")
        self.assertEqual(hub.answer, "Skipped · not a question")

    def test_partial_question_clears_previous_answer(self) -> None:
        hub = LiveHub()
        hub.apply({"type": "question", "text": "What is a mutex?", "source": "spoken"})
        hub.apply({"type": "answer", "text": "A mutex is a lock.", "source": "spoken"})
        hub.apply(
            {
                "type": "partial_question",
                "text": "How does a semaphore differ?",
                "source": "spoken",
            }
        )
        self.assertEqual(hub.question, "How does a semaphore differ?")
        self.assertEqual(hub.answer, "")
        self.assertEqual(hub.snapshot()["answer"], "")


class HubStatusTests(unittest.TestCase):
    def test_thinking_does_not_clear_listening(self) -> None:
        hub = LiveHub()
        hub.apply({"type": "status", "state": "listening", "detail": "Capturing question"})
        self.assertTrue(hub.listening)
        hub.apply({"type": "status", "state": "thinking", "detail": "Transcribing…"})
        self.assertTrue(hub.listening)
        hub.apply({"type": "status", "state": "ready", "detail": "Paused"})
        self.assertFalse(hub.listening)

    def test_skip_clears_streamed_draft(self) -> None:
        hub = LiveHub()
        hub.apply({"type": "question", "text": "thanks everyone", "source": "spoken"})
        hub.apply({"type": "answer_delta", "text": "You're welcome.", "source": "spoken"})
        hub.apply(
            {
                "type": "skip",
                "reason": "not_a_question",
                "text": "thanks everyone",
                "detail": "Skipped · not a question",
                "retryable": True,
                "source": "spoken",
            }
        )
        self.assertEqual(hub.answer, "Skipped · not a question")
        self.assertFalse(hub._drafting)


class HubRestoreTests(unittest.TestCase):
    def test_restore_sets_snapshot_pairs(self) -> None:
        from ai_interview.session import InterviewSession, TurnRecord

        session = InterviewSession()
        session.add_turn(
            TurnRecord(
                index=1,
                question="What is a mutex?",
                answer="A lock.",
                listen_ms=1,
                stt_ms=1,
                llm_ms=1,
                llm_first_ms=1,
                total_ms=2,
            )
        )
        session.add_turn(
            TurnRecord(
                index=2,
                question="Follow up?",
                answer="Yes.",
                listen_ms=1,
                stt_ms=1,
                llm_ms=1,
                llm_first_ms=1,
                total_ms=2,
            )
        )
        session.active = False
        hub = LiveHub()
        hub.restore(session)
        snap = hub.snapshot()
        self.assertEqual(snap["question"], "Follow up?")
        self.assertEqual(snap["pairs"][0]["question"], "Follow up?")
        self.assertEqual(len(snap["pairs"]), 2)
        self.assertFalse(snap["session"]["active"])

    def test_session_resume_reloads_pairs(self) -> None:
        from ai_interview.session import InterviewSession, TurnRecord

        session = InterviewSession()
        session.add_turn(
            TurnRecord(
                index=1,
                question="What is a mutex?",
                answer="A lock.",
                listen_ms=1,
                stt_ms=1,
                llm_ms=1,
                llm_first_ms=1,
                total_ms=2,
            )
        )
        hub = LiveHub()
        hub.apply({"type": "session_resume", "session": session.snapshot()})
        self.assertEqual(hub.question, "What is a mutex?")
        self.assertEqual(hub.pairs[0]["answer"], "A lock.")


class HubSnapshotTests(unittest.TestCase):
    def test_snapshot_shape_and_listening(self) -> None:
        hub = LiveHub()
        snap = hub.snapshot()
        self.assertEqual(snap["type"], "snapshot")
        self.assertFalse(snap["listening"])
        self.assertEqual(snap["question"], "")
        self.assertEqual(snap["answer"], "")
        self.assertEqual(snap["source"], "")
        self.assertEqual(snap["pairs"], [])
        self.assertIsNone(snap["session"])
        self.assertEqual(snap["answer_mode"], "spoken_45")
        self.assertEqual(len(snap["answer_modes"]), 5)

        hub.apply({"type": "status", "state": "listening", "detail": "Capturing question"})
        hub.apply({"type": "question", "text": "What is a mutex?", "source": "spoken"})
        hub.apply({"type": "answer_delta", "text": "A lock", "source": "spoken"})
        live = hub.snapshot()
        self.assertTrue(live["listening"])
        self.assertEqual(live["question"], "What is a mutex?")
        self.assertEqual(live["answer"], "A lock")
        self.assertEqual(live["source"], "spoken")
        self.assertEqual(live["pairs"], [])
        self.assertEqual(live["talking_points"]["bullets"], ["A lock"])

    def test_qa_prepending_and_session_start_reset(self) -> None:
        hub = LiveHub()
        hub.apply(
            {
                "type": "qa",
                "question": "What is a mutex?",
                "answer": "A lock.",
                "source": "spoken",
            }
        )
        hub.apply(
            {
                "type": "qa",
                "question": "Follow up?",
                "answer": "Yes.",
                "source": "typed",
            }
        )
        snap = hub.snapshot()
        self.assertEqual(snap["pairs"][0]["question"], "Follow up?")
        self.assertEqual(snap["pairs"][1]["question"], "What is a mutex?")
        self.assertEqual(len(snap["pairs"]), 2)

        hub.apply(
            {
                "type": "session_start",
                "session": {"id": "abc", "active": True, "turns": [], "stats": {"questions": 0}},
            }
        )
        reset = hub.snapshot()
        self.assertEqual(reset["question"], "")
        self.assertEqual(reset["answer"], "")
        self.assertEqual(reset["pairs"], [])
        self.assertEqual(reset["session"]["id"], "abc")
        self.assertIsNone(reset["talking_points"])

    def test_duplicate_qa_does_not_duplicate_pairs(self) -> None:
        hub = LiveHub()
        pair = {"type": "qa", "question": "Q", "answer": "A", "source": "spoken"}
        hub.apply(pair)
        hub.apply(pair)
        self.assertEqual(len(hub.snapshot()["pairs"]), 1)


class HubContextTests(unittest.IsolatedAsyncioTestCase):
    async def test_context_updated_and_snapshot(self) -> None:
        hub = LiveHub()
        await hub.submit_context(role="Staff SWE", company="Acme", resume="Python.")
        snap = hub.snapshot()
        self.assertEqual(snap["context"]["role"], "Staff SWE")
        self.assertEqual(snap["context"]["company"], "Acme")
        self.assertIsNone(snap["session"])

    def test_session_start_syncs_empty_context(self) -> None:
        hub = LiveHub()
        hub.context = {
            "role": "Old",
            "company": "OldCo",
            "job_description": "",
            "resume": "",
        }
        hub.apply(
            {
                "type": "session_start",
                "session": {
                    "id": "abc",
                    "active": True,
                    "turns": [],
                    "stats": {"questions": 0},
                    "context": {
                        "role": "",
                        "company": "",
                        "job_description": "",
                        "resume": "",
                    },
                },
            }
        )
        self.assertEqual(hub.context["role"], "")
        self.assertEqual(hub.snapshot()["context"]["company"], "")

    def test_attach_pipeline_seeds_pending_context(self) -> None:
        from ai_interview.pipeline import InterviewPipeline

        hub = LiveHub()
        hub.context = {
            "role": "PM",
            "company": "Initech",
            "job_description": "",
            "resume": "TPS reports.",
        }
        pipeline = InterviewPipeline(lambda event: None)
        hub.attach_pipeline(pipeline)
        self.assertEqual(pipeline._pending_context["company"], "Initech")
        self.assertEqual(pipeline._pending_answer_mode, "spoken_45")

    def test_snapshot_includes_empty_context(self) -> None:
        hub = LiveHub()
        snap = hub.snapshot()
        self.assertEqual(
            snap["context"],
            {"role": "", "company": "", "job_description": "", "resume": ""},
        )
        self.assertEqual(snap["answer_mode"], "spoken_45")

    async def test_answer_mode_updated_and_snapshot(self) -> None:
        hub = LiveHub()
        mode = await hub.submit_answer_mode("star")
        snap = hub.snapshot()
        self.assertEqual(mode, "star")
        self.assertEqual(snap["answer_mode"], "star")
        self.assertIsNone(snap["session"])

    def test_attach_pipeline_seeds_pending_answer_mode(self) -> None:
        from ai_interview.pipeline import InterviewPipeline

        hub = LiveHub()
        hub.answer_mode = "bullets"
        pipeline = InterviewPipeline(lambda event: None)
        hub.attach_pipeline(pipeline)
        self.assertEqual(pipeline._pending_answer_mode, "bullets")

    async def test_start_new_session_clears_live_pairs(self) -> None:
        import tempfile
        from pathlib import Path

        from ai_interview.pipeline import InterviewPipeline
        from ai_interview.store import SessionStore

        tmp = tempfile.TemporaryDirectory()
        store = SessionStore(Path(tmp.name) / "sessions.db")
        hub = LiveHub()
        hub.apply({"type": "qa", "question": "Q", "answer": "A", "source": "spoken"})
        pipeline = InterviewPipeline(hub.publish, store=store)
        hub.attach_pipeline(pipeline)
        await hub.start_new_session()
        snap = hub.snapshot()
        self.assertEqual(snap["pairs"], [])
        self.assertEqual(snap["question"], "")
        self.assertTrue(snap["session"]["active"])
        store.close()
        tmp.cleanup()
