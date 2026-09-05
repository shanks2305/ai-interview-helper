from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ai_interview.cli import desktop_api_base, desktop_process_env, lan_access_url, loopback_connect_host


class DesktopSpawnEnvTests(unittest.TestCase):
    def test_cli_disables_electron_api_spawn(self) -> None:
        with patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            env = desktop_process_env(host="127.0.0.1", port=8000, spawn_api=False)
        self.assertEqual(env["AI_INTERVIEW_SPAWN_API"], "0")
        self.assertEqual(env["AI_INTERVIEW_API"], "http://127.0.0.1:8000")

    def test_wildcard_bind_points_electron_at_loopback(self) -> None:
        self.assertEqual(loopback_connect_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(desktop_api_base("0.0.0.0", 9000), "http://127.0.0.1:9000")
        env = desktop_process_env(host="0.0.0.0", port=9000, spawn_api=False)
        self.assertEqual(env["AI_INTERVIEW_API"], "http://127.0.0.1:9000")

    def test_lan_url_uses_token(self) -> None:
        url = lan_access_url("192.168.1.4", 8000, "abc")
        self.assertEqual(url, "http://192.168.1.4:8000/live/?token=abc")
