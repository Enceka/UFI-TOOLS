"""Native control layer tests (hostapd config, LAN parsing, MAC lists)."""

from __future__ import annotations

import os
import tempfile
import types
import unittest

from ufitools.config import Config
from ufitools.control import (
    ControlError,
    SystemControl,
    auth_mode,
    parse_hostapd,
    render_hostapd,
)

HOSTAPD = """# e5 hotspot
interface=wlan0
driver=nl80211
ssid=E5-Linux
hw_mode=a
channel=149
country_code=CN
ieee80211d=1
wpa=2
wpa_passphrase=12345678
wpa_key_mgmt=WPA-PSK
rsn_pairwise=CCMP
max_num_sta=8
ignore_broadcast_ssid=0
"""

DNSMASQ = """interface=wlan0
dhcp-range=192.168.78.10,192.168.78.200,12h
"""


class ControlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.conf = os.path.join(self._tmp.name, "e5.conf")
        with open(self.conf, "w", encoding="utf-8") as handle:
            handle.write(HOSTAPD)
        self.dnsmasq = os.path.join(self._tmp.name, "e5-hotspot.conf")
        with open(self.dnsmasq, "w", encoding="utf-8") as handle:
            handle.write(DNSMASQ)

        config = Config(data_dir=os.path.join(self._tmp.name, "data"))
        config.update({
            "hotspot_conf": self.conf,
            "dnsmasq_conf": self.dnsmasq,
            "wlan_interface": "definitely-not-an-interface0",
            "hotspot_unit": "",        # no systemd in the test environment
            "mobile_data_unit": "",
        })
        self.app = types.SimpleNamespace(config=config)
        self.control = SystemControl(self.app)

    def tearDown(self):
        self._tmp.cleanup()

    # -- parsing -----------------------------------------------------------
    def test_parse_hostapd_ignores_comments_and_blank_lines(self):
        values = parse_hostapd(HOSTAPD)
        self.assertEqual(values["ssid"], "E5-Linux")
        self.assertEqual(values["channel"], "149")
        self.assertNotIn("# e5 hotspot", values)

    def test_render_round_trip(self):
        values = parse_hostapd(HOSTAPD)
        self.assertEqual(parse_hostapd(render_hostapd(values)), values)

    def test_auth_mode_mapping(self):
        self.assertEqual(auth_mode({"wpa": "0", "auth_algs": "1"}), "OPEN")
        self.assertEqual(auth_mode({"wpa": "2", "wpa_key_mgmt": "WPA-PSK"}), "WPA2(AES)-PSK")
        self.assertEqual(auth_mode({"wpa": "2", "wpa_key_mgmt": "SAE"}), "WPA3-PSK")
        self.assertEqual(auth_mode({"wpa": "2", "wpa_key_mgmt": "WPA-PSK SAE"}),
                         "WPA2-PSK/WPA3-PSK")

    # -- status ------------------------------------------------------------
    def test_hotspot_status_from_the_base_config(self):
        status = self.control.hotspot_status()
        self.assertEqual(status["ssid"], "E5-Linux")
        self.assertEqual(status["psk"], "12345678")
        self.assertEqual(status["channel"], "149")
        self.assertEqual(status["auth"], "WPA2(AES)-PSK")
        self.assertFalse(status["hidden"])
        self.assertFalse(status["using_managed_conf"])

    def test_lan_status_reads_the_dhcp_range(self):
        lan = self.control.lan_status()
        self.assertEqual(lan["dhcpStart"], "192.168.78.10")
        self.assertEqual(lan["dhcpEnd"], "192.168.78.200")
        self.assertTrue(lan["dhcpEnabled"])

    # -- writes ------------------------------------------------------------
    def test_configure_writes_a_managed_conf(self):
        result = self.control.configure_hotspot({"SSID": "E5-New", "Password": "abcdefgh",
                                                 "channel": "36", "ApMaxStationNumber": "4"})
        self.assertTrue(os.path.isfile(result["conf"]))
        values = parse_hostapd(open(result["conf"], encoding="utf-8").read())
        self.assertEqual(values["ssid"], "E5-New")
        self.assertEqual(values["wpa_passphrase"], "abcdefgh")
        self.assertEqual(values["channel"], "36")
        self.assertEqual(values["max_num_sta"], "4")
        # The managed file is what is reported as current from now on.
        self.assertTrue(self.control.hotspot_status()["using_managed_conf"])

    def test_configure_accepts_snake_case_too(self):
        result = self.control.configure_hotspot({"ssid": "Lower", "psk": "12345678"})
        values = parse_hostapd(open(result["conf"], encoding="utf-8").read())
        self.assertEqual(values["ssid"], "Lower")
        self.assertEqual(values["wpa_passphrase"], "12345678")

    def test_open_network_drops_the_passphrase(self):
        result = self.control.configure_hotspot({"AuthMode": "OPEN"})
        values = parse_hostapd(open(result["conf"], encoding="utf-8").read())
        self.assertEqual(values["wpa"], "0")
        self.assertNotIn("wpa_passphrase", values)
        self.assertEqual(auth_mode(values), "OPEN")

    def test_hidden_ssid_flag(self):
        result = self.control.configure_hotspot({"ApBroadcastDisabled": "0"})
        values = parse_hostapd(open(result["conf"], encoding="utf-8").read())
        self.assertEqual(values["ignore_broadcast_ssid"], "1")
        self.assertTrue(self.control.hotspot_status()["hidden"])

    def test_secured_network_requires_a_password(self):
        base = parse_hostapd(HOSTAPD)
        base.pop("wpa_passphrase")
        with open(self.conf, "w", encoding="utf-8") as handle:
            handle.write(render_hostapd(base))
        with self.assertRaises(ControlError):
            self.control.configure_hotspot({"SSID": "NoPass"})

    def test_client_access_writes_a_mac_list(self):
        result = self.control.set_client_access("deny", ["aa:bb:cc:dd:ee:ff"])
        self.assertEqual(result["count"], 1)
        self.assertTrue(os.path.isfile(result["list"]))
        values = parse_hostapd(open(self.control._managed_conf(), encoding="utf-8").read())
        self.assertEqual(values["macaddr_acl"], "1")
        self.assertEqual(values["deny_mac_file"], result["list"])

    def test_performance_without_cpufreq_is_reported(self):
        status = self.control.performance_status()
        self.assertIn("supported", status)
        if not status["supported"]:
            with self.assertRaises(ControlError):
                self.control.set_performance(True)

    def test_led_without_leds_is_reported(self):
        status = self.control.led_status()
        self.assertIn("supported", status)
        if not status["supported"]:
            with self.assertRaises(ControlError):
                self.control.set_led(True)

    def test_samba_requires_a_unit(self):
        with self.assertRaises(ControlError):
            self.control.set_samba(True)

    def test_mobile_data_without_unit_is_reported(self):
        with self.assertRaises(ControlError):
            self.control.set_mobile_data(True)


if __name__ == "__main__":
    unittest.main()
