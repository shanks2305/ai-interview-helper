from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ai_interview.auth import (
    client_needs_token,
    ensure_access_token,
    http_path_requires_token,
    is_authorized,
    is_loopback_host,
    lan_auth_enabled,
    provided_token,
    tokens_match,
)


class BindHostTests(unittest.TestCase):
    def test_loopback_and_wildcard(self) -> None:
        self.assertTrue(is_loopback_host("127.0.0.1"))
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertTrue(is_loopback_host("127.4.4.4"))
        self.assertFalse(is_loopback_host("0.0.0.0"))
        self.assertFalse(is_loopback_host("192.168.1.20"))
        self.assertFalse(is_loopback_host("::"))

    def test_lan_auth_only_when_bound_public(self) -> None:
        self.assertFalse(lan_auth_enabled("127.0.0.1"))
        self.assertTrue(lan_auth_enabled("0.0.0.0"))
        self.assertTrue(client_needs_token("192.168.1.20", bind="0.0.0.0"))
        self.assertFalse(client_needs_token("127.0.0.1", bind="0.0.0.0"))
        self.assertFalse(client_needs_token("192.168.1.20", bind="127.0.0.1"))


class TokenMatchTests(unittest.TestCase):
    def test_rejects_missing_or_wrong_token(self) -> None:
        self.assertFalse(tokens_match("", "secret"))
        self.assertFalse(tokens_match("nope", "secret"))
        self.assertTrue(tokens_match("secret", "secret"))

    def test_header_and_query_extraction(self) -> None:
        self.assertEqual(provided_token(query={"token": "abc"}), "abc")
        self.assertEqual(
            provided_token(query={}, headers={"x-interview-token": "from-header"}),
            "from-header",
        )
        self.assertEqual(
            provided_token(query={}, headers={"authorization": "Bearer xyz"}),
            "xyz",
        )

    def test_api_paths_need_token_on_lan(self) -> None:
        self.assertTrue(http_path_requires_token("/api/sessions"))
        self.assertTrue(http_path_requires_token("/api/ask"))
        self.assertFalse(http_path_requires_token("/health"))
        self.assertFalse(http_path_requires_token("/live/"))

    def test_authorization_decision(self) -> None:
        self.assertTrue(
            is_authorized(client_host="127.0.0.1", provided="", bind="0.0.0.0", expected="secret")
        )
        self.assertFalse(
            is_authorized(client_host="10.0.0.8", provided="", bind="0.0.0.0", expected="secret")
        )
        self.assertTrue(
            is_authorized(
                client_host="10.0.0.8", provided="secret", bind="0.0.0.0", expected="secret"
            )
        )


class EnsureTokenTests(unittest.TestCase):
    def test_loopback_does_not_mint_token(self) -> None:
        with patch.dict(os.environ, {"AI_INTERVIEW_TOKEN": ""}):
            self.assertIsNone(ensure_access_token("127.0.0.1"))

    def test_public_bind_mints_and_reuses_token(self) -> None:
        with patch.dict(os.environ, {"AI_INTERVIEW_TOKEN": ""}):
            minted = ensure_access_token("0.0.0.0")
            self.assertTrue(minted)
            self.assertEqual(os.environ["AI_INTERVIEW_TOKEN"], minted)
            self.assertEqual(ensure_access_token("0.0.0.0"), minted)
