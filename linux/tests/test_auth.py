"""Signature, whitelist and token tests.

The signature vectors were produced independently with Node's ``crypto``
implementing the same documented algorithm, so they catch an indexing or
byte/hex mix-up in the Python port rather than merely restating it.
"""

from __future__ import annotations

import unittest

from ufitools import auth


VECTORS = [
    ("POST", "/api/user", "1718438543772",
     "92fdcf86c1850139dcdc67bf501bf270d7c39675993ccd69fdb7e5f9160e0a89"),
    ("GET", "/api/AT", "0",
     "58df4f3e4c78fe7c12a99603021d57bde56156c88ccd5e2dc0ecdfcee5896fbe"),
    ("GET", "/api/proxy/--http://example.com/a", "999",
     "6145d58cc4d21b24b04f376cb400cce46c7f64c1659fe40fe9a39bf7317ab899"),
]


class SignatureTests(unittest.TestCase):
    def test_known_vectors(self):
        for method, path, stamp, expected in VECTORS:
            raw = "minikano%s%s%s" % (method, path, stamp)
            self.assertEqual(auth.hmac_signature(auth.REQUEST_SECRET_KEY, raw), expected)

    def test_secret_matches_the_android_constant(self):
        self.assertEqual(auth.REQUEST_SECRET_KEY, "minikano_kOyXz0Ciz4V7wR0IeKmJFYFQ20jd")

    def test_default_token_hash(self):
        self.assertEqual(
            auth.sha256_hex("admin"),
            "8c6976e5b5410415bde908bd4dee15dfb167a9c873fc4bb8a81f6f2ab448a918",
        )


class NormalizePathTests(unittest.TestCase):
    def test_decodes_twice_and_squashes_slashes(self):
        self.assertEqual(auth.normalize_path("/api//get%255Ftheme"), "/api/get_theme")

    def test_adds_leading_slash(self):
        self.assertEqual(auth.normalize_path("api/AT"), "/api/AT")

    def test_backslashes_are_slashes(self):
        self.assertEqual(auth.normalize_path("\\api\\AT"), "/api/AT")

    def test_leading_slashes_only_normalised_for_proxy(self):
        self.assertEqual(auth.normalize_leading_slashes("//api/proxy/--http://x/y"),
                         "/api/proxy/--http://x/y")
        # normalize_leading_slashes must not URL-decode
        self.assertEqual(auth.normalize_leading_slashes("/api/%2570"), "/api/%2570")


class WhitelistTests(unittest.TestCase):
    def test_public_paths(self):
        for path in ("/api/get_custom_head", "/api/version_info", "/api/need_token",
                     "/api/get_theme", "/api/SELinux", "/api/uploads/a.png", "/index.html", "/"):
            self.assertTrue(auth.is_public_path(auth.normalize_path(path)), path)

    def test_protected_paths(self):
        for path in ("/api/AT", "/api/root_shell", "/api/baseDeviceInfo", "/api/set_token"):
            self.assertFalse(auth.is_public_path(auth.normalize_path(path)), path)


class TokenTests(unittest.TestCase):
    def test_normalize_token_hashes_plaintext(self):
        self.assertEqual(auth.normalize_token("admin"), auth.sha256_hex("admin"))

    def test_normalize_token_keeps_hash(self):
        digest = auth.sha256_hex("secret")
        self.assertEqual(auth.normalize_token(digest.upper()), digest)

    def test_weak_token_detection(self):
        self.assertTrue(auth.is_weak_token(auth.sha256_hex("admin")))
        self.assertFalse(auth.is_weak_token(auth.sha256_hex("abcd1234")))

    def test_token_rules(self):
        self.assertIsNotNone(auth.check_token_rules(""))
        self.assertIsNotNone(auth.check_token_rules("short1"))
        self.assertIsNotNone(auth.check_token_rules("onlylettersx"))
        self.assertIsNotNone(auth.check_token_rules("1234567890"))
        self.assertIsNone(auth.check_token_rules("abcd1234"))


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.token = auth.sha256_hex("abcd1234")
        self.checker = auth.AuthChecker(
            token_source=lambda: self.token,
            enabled_source=lambda: True,
            now_ms=lambda: 1718438543772,
        )

    def _headers(self, method, path, **override):
        stamp = "1718438543772"
        headers = {
            "kano-t": stamp,
            "kano-sign": auth.hmac_signature(auth.REQUEST_SECRET_KEY,
                                             "minikano%s%s%s" % (method, path, stamp)),
            "authorization": self.token,
        }
        headers.update(override)
        return headers

    def test_accepts_valid_request(self):
        self.checker.check("GET", "/api/AT", self._headers("GET", "/api/AT"))

    def test_header_names_are_case_insensitive(self):
        headers = {k.upper(): v for k, v in self._headers("GET", "/api/AT").items()}
        self.checker.check("GET", "/api/AT", headers)

    def test_rejects_bad_token(self):
        headers = self._headers("GET", "/api/AT", authorization=auth.sha256_hex("wrong"))
        with self.assertRaises(auth.AuthError):
            self.checker.check("GET", "/api/AT", headers)

    def test_rejects_bad_signature(self):
        headers = self._headers("GET", "/api/AT", **{"kano-sign": "deadbeef"})
        with self.assertRaises(auth.AuthError):
            self.checker.check("GET", "/api/AT", headers)

    def test_rejects_missing_headers(self):
        with self.assertRaises(auth.AuthError):
            self.checker.check("GET", "/api/AT", {})

    def test_public_path_needs_nothing(self):
        self.checker.check("GET", "/api/version_info", {})
        self.checker.check("GET", "/index.html", {})

    def test_proxy_signs_the_raw_path(self):
        path = "/api/proxy/--http://example.com/a"
        self.checker.check("GET", path, self._headers("GET", path))

    def test_disabled_token_lets_everything_through(self):
        checker = auth.AuthChecker(token_source=lambda: self.token, enabled_source=lambda: False)
        checker.check("POST", "/api/root_shell", {})

    def test_skew_check_when_enabled(self):
        checker = auth.AuthChecker(
            token_source=lambda: self.token,
            enabled_source=lambda: True,
            max_skew_ms=1000,
            now_ms=lambda: 1718438549999,
        )
        with self.assertRaises(auth.AuthError):
            checker.check("GET", "/api/AT", self._headers("GET", "/api/AT"))


if __name__ == "__main__":
    unittest.main()
