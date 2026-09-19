"""Modem snapshot tests: AT parsing and the anti-flood cache."""

from __future__ import annotations

import threading
import time
import unittest

from ufitools.at import ATError
from ufitools.modem import ModemSnapshot, derive

CSQ = "+CSQ: 20,99\r\nOK"
CESQ = "+CESQ: 60,99,255,255,20,60\r\nOK"
COPS = '+COPS: 0,0,"CHN-UNICOM",7\r\nOK'
CREG = "+CREG: 0,1\r\nOK"
CEREG = "+CEREG: 0,1\r\nOK"


class DeriveTests(unittest.TestCase):
    def test_signal_is_converted_to_dbm_and_bars(self):
        out = derive({"csq": CSQ, "cesq": CESQ})
        self.assertEqual(out["rssi"], "-73")           # -113 + 2*20
        self.assertEqual(out["network_rssi"], "-73")
        self.assertEqual(out["lte_rsrp"], "-80")       # -140 + 60
        self.assertEqual(out["Z5g_rsrp"], "-80")
        self.assertEqual(out["lte_rsrq"], "-9.5")      # -19.5 + 20*0.5
        self.assertEqual(out["network_signalbar"], "5")

    def test_operator_and_access_technology(self):
        out = derive({"cops": COPS})
        self.assertEqual(out["network_provider"], "CHN-UNICOM")
        self.assertEqual(out["network_type"], "4G")

    def test_registration_status(self):
        self.assertEqual(derive({"cereg": CEREG})["reg_status"], "1")
        self.assertEqual(derive({"creg": CREG})["reg_status"], "1")

    def test_identity_fields(self):
        out = derive({
            "imei": "861234567890123\r\nOK",
            "imsi": "460011234567890\r\nOK",
            "iccid": "+CCID: 89860626690008016183\r\nOK",
            "cnum": '+CNUM: "","+8613800000000",129\r\nOK',
        })
        self.assertEqual(out["imei"], "861234567890123")
        self.assertEqual(out["imsi"], "460011234567890")
        self.assertEqual(out["iccid"], "89860626690008016183")
        self.assertEqual(out["msisdn"], "+8613800000000")

    def test_apn_and_address_from_bearer_info(self):
        out = derive({"cGCONTRDP": '+CGCONTRDP: 1,5,"3gnet","10.133.137.8","10.1.1.1"\r\nOK'})
        self.assertEqual(out["apn"], "3gnet")
        self.assertEqual(out["ipv4_wan_ipaddr"], "10.133.137.8")

    def test_empty_input_is_safe(self):
        self.assertEqual(derive({}), {})

    def test_error_response_is_ignored(self):
        self.assertEqual(derive({"csq": "ERROR", "cesq": "ERROR"}), {})


class _FakeAT:
    """Counts how many times the command channel was used."""

    def __init__(self, available=True, fail=False):
        self.calls = []
        self._available = available
        self._fail = fail

    def available(self):
        return self._available

    def run(self, command, timeout=None):
        self.calls.append(command)
        if self._fail:
            raise ATError("no answer")
        return "+CSQ: 20,99\r\nOK" if command == "AT+CSQ" else "OK"


class SnapshotTests(unittest.TestCase):
    def test_refresh_reads_every_command(self):
        fake = _FakeAT()
        snapshot = ModemSnapshot(fake, interval=60.0)
        data = snapshot.refresh_now()
        self.assertEqual(len(fake.calls), 11)
        self.assertEqual(data["rssi"], "-73")

    def test_failures_do_not_raise(self):
        snapshot = ModemSnapshot(_FakeAT(fail=True), interval=60.0)
        self.assertEqual(snapshot.refresh_now(), {})

    def test_interval_zero_disables_at(self):
        fake = _FakeAT()
        snapshot = ModemSnapshot(fake, interval=0.0)
        self.assertEqual(snapshot.as_dict(), {})
        time.sleep(0.05)
        self.assertEqual(fake.calls, [])

    def test_cache_is_served_without_refreshing(self):
        fake = _FakeAT()
        snapshot = ModemSnapshot(fake, interval=3600.0)
        snapshot.refresh_now()
        calls_after_first = len(fake.calls)
        snapshot.get("rssi")
        snapshot.get("rssi")
        # The cache is fresh, so no background refresh is kicked off.
        self.assertEqual(len(fake.calls), calls_after_first)
        self.assertEqual(snapshot.get("rssi"), "-73")

    def test_no_backend_means_no_commands(self):
        snapshot = ModemSnapshot(_FakeAT(available=False), interval=1.0)
        self.assertEqual(snapshot.get("rssi"), "")
        time.sleep(0.05)
        self.assertEqual(snapshot.age, -1.0)

    def test_concurrent_kicks_do_not_stampede(self):
        fake = _FakeAT()
        snapshot = ModemSnapshot(fake, interval=0.0)
        threads = [threading.Thread(target=lambda: snapshot.get("rssi")) for _ in range(20)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
