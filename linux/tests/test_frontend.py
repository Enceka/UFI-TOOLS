"""Contract tests between the web frontend and this backend.

The frontend and the backend speak a vocabulary that has to stay in step: every
``action`` the page can send must be a name the backend recognises, and every
field it polls must be one the backend answers.  These tests read the shipped
frontend and cross-check it against the backend's own tables, so a rename on one
side cannot silently break the other.

They also guard the removals: no vendor-login machinery and no vendor naming may
creep back into the page.
"""

from __future__ import annotations

import json
import os
import re
import unittest

from ufitools.api.ui_compat import RETIRED_COMMANDS, _SUPPORTED_ACTIONS, _UNSUPPORTED_ACTIONS

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(HERE, "..", "www")

ACTION_RE = re.compile(r"""\baction\s*:\s*['"]([A-Za-z_][A-Za-z0-9_]*)['"]""")
CMD_RE = re.compile(r"""\bcmd\s*:\s*['"]([A-Za-z0-9_,]+)['"]""")
FIELD_LIST_RE = re.compile(r"""cmd=([A-Za-z0-9_,]+)""")
POLL_RE = re.compile(r"""const cmd = '([A-Za-z0-9_,]+)'""")

#: Fields the frontend polls that the backend deliberately does not answer
#: (they were part of the vendor session / radio control surface).
KNOWN_UNANSWERED = set(RETIRED_COMMANDS)


def read(*parts) -> str:
    with open(os.path.join(WWW, *parts), encoding="utf-8") as handle:
        return handle.read()


def all_frontend_js() -> str:
    script_dir = os.path.join(WWW, "script")
    chunks = []
    for name in sorted(os.listdir(script_dir)):
        if name.endswith(".js"):
            chunks.append(read("script", name))
    return "\n".join(chunks)


class ActionContractTests(unittest.TestCase):
    def test_every_frontend_action_is_recognised_by_the_backend(self):
        recognised = set(_SUPPORTED_ACTIONS) | set(_UNSUPPORTED_ACTIONS)
        used = set(ACTION_RE.findall(all_frontend_js()))
        self.assertTrue(used, "no actions found in the frontend?")
        unknown = sorted(used - recognised)
        self.assertEqual(unknown, [], "frontend sends actions the backend does not know: %s" % unknown)

    def test_retired_actions_are_not_sent_by_the_frontend(self):
        used = set(ACTION_RE.findall(all_frontend_js()))
        retired = sorted(used & set(_UNSUPPORTED_ACTIONS) & {"LOGIN", "LOGIN_MULTI_USER", "LOGOUT"})
        self.assertEqual(retired, [], "frontend still performs vendor login: %s" % retired)

    def test_supported_actions_are_not_also_marked_unsupported(self):
        overlap = set(_SUPPORTED_ACTIONS) & set(_UNSUPPORTED_ACTIONS)
        self.assertEqual(overlap, set())


class FieldContractTests(unittest.TestCase):
    def _polled_fields(self):
        names = set()
        for match in POLL_RE.findall(read("script", "requests.js")):
            names.update(match.split(","))
        for match in FIELD_LIST_RE.findall(all_frontend_js()):
            names.update(match.split(","))
        for match in CMD_RE.findall(all_frontend_js()):
            names.update(match.split(","))
        return {name for name in names if name}

    def test_frontend_does_not_poll_retired_fields(self):
        # The polling list is allowed to be a superset of what the backend
        # answers (unknown names come back as ""), but the retired ones would
        # make the request fail outright -- so they must not be in a batch.
        for match in POLL_RE.findall(read("script", "requests.js")):
            for name in match.split(","):
                self.assertNotIn(name, RETIRED_COMMANDS, "poll list asks for %s" % name)

    def test_status_poll_contains_the_core_fields(self):
        poll = POLL_RE.search(read("script", "requests.js"))
        self.assertIsNotNone(poll)
        fields = poll.group(1).split(",")
        for expected in ("ppp_status", "battery_value", "network_type", "lan_ipaddr",
                         "wifi_access_sta_num", "monthly_rx_bytes", "loginfo"):
            self.assertIn(expected, fields)


class VendorLeftoverTests(unittest.TestCase):
    def test_no_vendor_protocol_naming_in_the_frontend(self):
        for name in ("main.js", "requests.js", "utils.js"):
            text = read("script", name)
            lowered = text.lower()
            for needle in ("goform", "goformid"):
                self.assertNotIn(needle, lowered, "%s still mentions %s" % (name, needle))

    def test_no_vendor_naming_anywhere_in_the_page(self):
        candidates = ["index.html", "manifest.json", "robots.txt"]
        for name in candidates:
            text = read(name)
            for needle in ("中兴", "某兴", "ZTE", "ZXE", "goform"):
                self.assertNotIn(needle, text, "%s still mentions %s" % (name, needle))
        for name in ("zh.json", "en.json", "ja.json", "vi.json"):
            text = read("lang", name)
            for needle in ("中兴", "某兴", "ZTE", "ZXE"):
                self.assertNotIn(needle, text, "lang/%s still mentions %s" % (name, needle))

    def test_login_form_has_no_vendor_password_field(self):
        html = read("index.html")
        self.assertNotIn("PWDINPUT", html)
        self.assertNotIn("某兴", html)
        self.assertNotIn("login_method", html)
        self.assertNotIn("noPassLogin", html)

    def test_login_does_not_call_a_vendor_endpoint(self):
        requests_js = read("script", "requests.js")
        self.assertNotIn("/goform/", requests_js)
        self.assertNotIn("set_cookie", requests_js)
        self.assertNotIn("get_cookie", requests_js)
        # The token is the only credential, and it is verified against our own API.
        self.assertIn("/ui/fields", requests_js)
        self.assertIn("/ui/action", requests_js)

    def test_language_packs_stay_parseable_and_share_keys(self):
        packs = {}
        for name in ("zh.json", "en.json", "ja.json", "vi.json"):
            packs[name] = json.loads(read("lang", name))
        reference = set(packs["zh.json"])
        for name, pack in packs.items():
            missing = sorted(reference - set(pack))
            self.assertEqual(missing, [], "%s is missing keys: %s" % (name, missing[:5]))

    def test_removed_login_keys_are_gone_from_every_language(self):
        for name in ("zh.json", "en.json", "ja.json", "vi.json"):
            pack = json.loads(read("lang", name))
            for key in ("token_div_2", "token_placeholder_pwd", "login_method_1",
                        "no_pass_login_btn", "token_note_3"):
                self.assertNotIn(key, pack, "%s still defines %s" % (name, key))


class ArithmeticTests(unittest.TestCase):
    def test_byte_fields_are_not_added_as_strings(self):
        # The backend answers every field as a string, as the vendor web API
        # did; "285708039" + "686847219" concatenates to ~250 PB.
        pair = re.compile(r"res\.[A-Za-z0-9_]*(?:bytes|thrpt)[A-Za-z0-9_]*\s*\+\s*res\.")
        self.assertEqual(pair.findall(all_frontend_js()), [])


class ShimTests(unittest.TestCase):
    def test_shim_does_not_touch_the_login_form(self):
        shim = read("..", "www-linux", "ufi-linux-shim.js")
        self.assertNotIn("PWDINPUT", shim)
        self.assertNotIn("adaptLoginForm", shim)

    def test_shim_is_valid_enough_to_parse(self):
        # A cheap structural check: balanced braces and the console entry point.
        shim = read("..", "www-linux", "ufi-linux-shim.js")
        self.assertEqual(shim.count("{"), shim.count("}"))
        self.assertIn("ufiLinux", shim)


if __name__ == "__main__":
    unittest.main()
