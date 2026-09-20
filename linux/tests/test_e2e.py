"""End-to-end tests: a real HTTP server, real signed requests.

This is the test that proves the refactor works as a drop-in backend for the
existing web frontend: requests are built with the same signature the browser
computes, and assertions are on the JSON shapes the frontend reads.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request

from ufitools import auth
from ufitools.app import Application
from ufitools.config import Config

INDEX_HTML = "<html><body>UFI-TOOLS TEST</body></html>"
HOSTAPD_CONF = "interface=wlan0\nssid=E5-Test\nhw_mode=a\nchannel=149\n" \
               "wpa=2\nwpa_passphrase=12345678\nwpa_key_mgmt=WPA-PSK\nmax_num_sta=8\n"
DNSMASQ_CONF = "interface=wlan0\ndhcp-range=192.168.78.10,192.168.78.200,12h\n"


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.www = os.path.join(root, "www")
        os.makedirs(cls.www)
        with open(os.path.join(cls.www, "index.html"), "w", encoding="utf-8") as handle:
            handle.write(INDEX_HTML)

        cls.hostapd = os.path.join(root, "hostapd.conf")
        with open(cls.hostapd, "w", encoding="utf-8") as handle:
            handle.write(HOSTAPD_CONF)
        cls.dnsmasq = os.path.join(root, "dnsmasq.conf")
        with open(cls.dnsmasq, "w", encoding="utf-8") as handle:
            handle.write(DNSMASQ_CONF)

        config = Config(data_dir=os.path.join(root, "data"))
        config.update({
            "hotspot_conf": cls.hostapd,
            "dnsmasq_conf": cls.dnsmasq,
            "hotspot_unit": "",          # no systemd in the test environment
            "mobile_data_unit": "",
            "wlan_interface": "ufi-test0",
        })
        cls.app = Application(config=config, static_root=cls.www, logger=lambda message: None)
        cls.server = cls.app.make_server(bind="127.0.0.1", port=0, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    # -- helpers -----------------------------------------------------------
    def request(self, method, path, body=None, token="admin", sign=True, headers=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        parsed = urllib.parse.urlsplit(url)
        payload = None
        if body is not None:
            payload = json.dumps(body).encode() if not isinstance(body, bytes) else body
        request = urllib.request.Request(url, data=payload)
        request.get_method = lambda: method
        if sign:
            stamp = "1718438543772"
            request.add_header("kano-t", stamp)
            request.add_header("kano-sign", auth.hmac_signature(
                auth.REQUEST_SECRET_KEY, "minikano%s%s%s" % (method.upper(), parsed.path, stamp)))
            request.add_header("authorization", auth.sha256_hex(token))
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def get_json(self, path, token="admin", **kwargs):
        status, body = self.request("GET", path, token=token, **kwargs)
        return status, (json.loads(body) if body else None)

    def post_json(self, path, body, token="admin"):
        status, payload = self.request("POST", path, body=body, token=token)
        return status, (json.loads(payload) if payload else None)

    # -- authentication ----------------------------------------------------
    def test_protected_endpoint_requires_headers(self):
        status, body = self.request("GET", "/api/baseDeviceInfo", sign=False)
        self.assertEqual(status, 401)
        self.assertEqual(body, b"")

    def test_wrong_token_is_rejected(self):
        self.assertEqual(self.request("GET", "/api/baseDeviceInfo", token="nope")[0], 401)

    def test_public_endpoints_need_no_headers(self):
        for path in ("/api/version_info", "/api/need_token", "/api/SELinux",
                     "/api/get_theme", "/api/get_custom_head"):
            status, body = self.request("GET", path, sign=False)
            self.assertEqual(status, 200, path)
            self.assertTrue(json.loads(body), path)

    # -- static frontend and the injected shim -----------------------------
    def test_index_is_served_with_the_shim_injected(self):
        status, body = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"UFI-TOOLS TEST", body)
        self.assertIn(b'/ufi-linux-shim.js', body)

    def test_shim_is_served_from_the_overlay(self):
        status, body = self.request("GET", "/ufi-linux-shim.js", sign=False)
        self.assertEqual(status, 200)
        self.assertIn(b'ufiLinux', body)
        self.assertIn("HIDDEN_BUTTONS".encode(), body)

    def test_unknown_asset_is_404(self):
        self.assertEqual(self.request("GET", "/style/missing.css")[0], 404)

    def test_single_page_app_fallback(self):
        status, body = self.request("GET", "/some/route")
        self.assertEqual(status, 200)
        self.assertIn(b"UFI-TOOLS TEST", body)

    def test_path_traversal_is_refused(self):
        self.assertIn(self.request("GET", "/../config.json")[0], (400, 403, 404))

    # -- device information (no vendor layer) ------------------------------
    def test_base_device_info_shape(self):
        status, data = self.get_json("/api/baseDeviceInfo")
        self.assertEqual(status, 200)
        for key in ("app_ver", "model", "battery", "cpu_temp", "cpu_temp_list", "cpu_usage",
                    "mem_usage", "cpuFreqInfo", "cpuUsageInfo", "memInfo", "daily_data",
                    "monthly_data", "internal_total_storage", "client_ip",
                    "is_reached_data_flow_limit", "modem"):
            self.assertIn(key, data, key)
        self.assertNotIn("device_mode", data)

    def test_conn_info_shape(self):
        status, data = self.get_json("/api/connInfo")
        self.assertEqual(status, 200)
        for key in ("tcp", "tcp_active", "tcp_other", "tcp6", "udp", "udp6", "unix"):
            self.assertIn(key, data["data"])

    def test_version_info_reports_frontend_fields(self):
        status, data = self.get_json("/api/version_info", sign=False)
        self.assertEqual(set(("app_ver", "app_ver_code", "model", "nickname", "accept_terms")),
                         set(data.keys()))

    def test_usb_status_shape(self):
        status, data = self.get_json("/api/usb_status")
        self.assertEqual(status, 200)
        self.assertIn("maxSpeed", data)
        self.assertIn("details", data)

    def test_platform_diagnostics(self):
        status, data = self.get_json("/api/platform")
        self.assertEqual(status, 200)
        self.assertIn("at_backend", data)
        self.assertEqual(data["ui_shim"], True)
        self.assertIn("samba_unit", data)

    # -- removed vendor endpoints are gone ---------------------------------
    def test_vendor_endpoints_are_gone(self):
        for path in ("/api/get_cookie", "/api/install_apk", "/api/adb_alive",
                     "/api/get_official_web_password", "/api/getSupportNrBandList"):
            status, data = self.get_json(path)
            self.assertEqual(status, 404, path)
            self.assertIn("error", data)

    # -- configuration -----------------------------------------------------
    def test_nickname_round_trip(self):
        self.assertEqual(self.post_json("/api/set_nickname", {"nickname": "客厅"})[0], 200)
        self.assertEqual(self.get_json("/api/version_info", sign=False)[1]["nickname"], "客厅")

    def test_theme_round_trip(self):
        self.assertEqual(self.post_json("/api/set_theme",
                                        {"themeColor": "42", "blurSwitch": "false"})[0], 200)
        _, theme = self.get_json("/api/get_theme", sign=False)
        self.assertEqual(theme["themeColor"], "42")
        self.assertEqual(theme["textColor"], "rgba(255, 255, 255, 1)")

    def test_data_limit_round_trip(self):
        status, _ = self.post_json("/api/set_data_limit", {
            "data_flow_limit_enabled": "1", "data_flow_max_limit": 10737418240,
            "data_flow_check_daily_or_monthly": "monthly", "data_check_reference": "android",
            "data_limit_status_forward_enabled": "1",
        })
        self.assertEqual(status, 200)
        _, data = self.get_json("/api/get_data_limit")
        self.assertEqual(data["data_flow_max_limit"], 10737418240)

    def test_weak_token_flag_and_rotation(self):
        self.assertTrue(self.get_json("/api/is_weak_token")[1]["is_weak_token"])
        self.assertEqual(self.post_json("/api/set_token", {"token": "abcd1234"})[0], 200)
        self.assertFalse(self.get_json("/api/is_weak_token", token="abcd1234")[1]["is_weak_token"])
        self.assertEqual(self.request("GET", "/api/baseDeviceInfo", token="admin")[0], 401)
        self.assertEqual(self.get_json("/api/baseDeviceInfo", token="abcd1234")[0], 200)
        # restore the default so the rest of the suite is unaffected
        self.app.config.set("login_token", auth.sha256_hex("admin"))
        self.assertEqual(self.get_json("/api/baseDeviceInfo")[0], 200)

    def test_invalid_token_is_refused(self):
        status, data = self.post_json("/api/set_token", {"token": "short"})
        self.assertEqual(status, 500)
        self.assertIn("error", data)

    # -- hardware bridges --------------------------------------------------
    def test_at_without_a_modem_answers_clearly(self):
        status, data = self.get_json("/api/AT?command=AT%2BCSQ&slot=0")
        self.assertEqual(status, 500)
        self.assertIn("AT指令执行错误", data["error"])

    def test_at_rejects_non_at_command(self):
        status, data = self.get_json("/api/AT?command=HELLO")
        self.assertEqual(status, 500)
        self.assertIn("AT", data["error"])

    def test_at_status_reports_backend(self):
        status, data = self.get_json("/api/at/status")
        self.assertEqual(status, 200)
        self.assertIn("backend", data)
        self.assertIn("poll_interval", data)

    def test_user_shell_runs(self):
        status, data = self.post_json("/api/user_shell", {"command": "echo ufi-ok"})
        self.assertEqual(status, 200)
        self.assertTrue(data["result"]["done"])
        self.assertIn("ufi-ok", data["result"]["content"])

    def test_root_shell_needs_advanced_mode(self):
        status, data = self.post_json("/api/root_shell", {"command": "id"})
        self.assertEqual(status, 500)
        self.assertIn("高级功能", data["error"])

        self.assertEqual(self.get_json("/api/smbPath?enable=1")[0], 200)
        self.assertTrue(self.app.advanced)
        status, data = self.post_json("/api/root_shell", {"command": "echo root-ok"})
        self.assertEqual(status, 200)
        self.assertIn("root-ok", data["result"]["content"])
        self.get_json("/api/smbPath?enable=0")
        self.assertFalse(self.app.advanced)

    # -- native control ----------------------------------------------------
    def test_overview_shape(self):
        status, data = self.get_json("/api/linux/overview")
        self.assertEqual(status, 200)
        for key in ("uptime", "memory", "thermal", "battery", "traffic", "mobile_data",
                    "hotspot", "lan", "clients", "performance", "led", "modem"):
            self.assertIn(key, data, key)

    def test_hotspot_reports_the_configured_ap(self):
        # Drop any managed config a previous test wrote, so this asserts on the
        # base configuration.
        managed = self.app.control._managed_conf()
        if os.path.isfile(managed):
            os.remove(managed)
        status, data = self.get_json("/api/linux/hotspot")
        self.assertEqual(status, 200)
        self.assertEqual(data["ssid"], "E5-Test")
        self.assertEqual(data["psk"], "12345678")
        self.assertEqual(data["channel"], "149")
        self.assertEqual(data["auth"], "WPA2(AES)-PSK")
        self.assertFalse(data["using_managed_conf"])

    def test_hotspot_configuration_is_persisted(self):
        status, data = self.post_json("/api/linux/hotspot",
                                      {"ssid": "E5-New", "psk": "abcdefgh", "channel": "36"})
        self.assertEqual(status, 200)
        _, current = self.get_json("/api/linux/hotspot")
        self.assertEqual(current["ssid"], "E5-New")
        self.assertEqual(current["channel"], "36")
        self.assertTrue(current["using_managed_conf"])
        self.assertIn("restarted", data["applied"])

    def test_lan_status_reads_dhcp_range(self):
        status, data = self.get_json("/api/linux/lan")
        self.assertEqual(status, 200)
        self.assertEqual(data["dhcpStart"], "192.168.78.10")
        self.assertEqual(data["dhcpEnd"], "192.168.78.200")

    def test_mobile_data_without_a_unit_reports_clearly(self):
        status, data = self.get_json("/api/linux/mobile-data")
        self.assertEqual(status, 200)
        self.assertIn("connected", data)
        status, data = self.post_json("/api/linux/mobile-data", {"enabled": True})
        self.assertEqual(status, 500)
        self.assertIn("单元", data["error"])

    def test_performance_status(self):
        status, data = self.get_json("/api/linux/performance")
        self.assertEqual(status, 200)
        self.assertIn("supported", data)

    def test_power_requires_a_valid_action(self):
        status, data = self.post_json("/api/linux/power", {"action": "explode"})
        self.assertEqual(status, 400)
        self.assertIn("error", data)

    def test_clients_endpoint(self):
        status, data = self.get_json("/api/linux/hotspot/clients")
        self.assertEqual(status, 200)
        self.assertIn("clients", data)

    # -- UI compatibility layer -------------------------------------------
    def action(self, name, **params):
        payload = dict(params)
        payload["action"] = name
        body = urllib.parse.urlencode(payload).encode()
        status, raw = self.request("POST", "/api/ui/action", body=body,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        return status, (json.loads(raw) if raw else None)

    def test_vendor_login_is_retired(self):
        # These only ever existed to drive a vendor session; asking for them is
        # refused rather than answered with a plausible fake.
        for name in ("LD", "RD", "wa_inner_version", "psw_fail_num_str", "login_lock_time"):
            status, data = self.get_json("/api/ui/fields?cmd=" + name)
            self.assertEqual(status, 500, name)
            self.assertIn("厂商登录已移除", data["error"])

    def test_login_and_logout_actions_are_retired(self):
        for name in ("LOGIN", "LOGIN_MULTI_USER", "LOGOUT"):
            status, data = self.action(name, password="anything")
            self.assertEqual(status, 500, name)
            self.assertIn("UFI-TOOLS 口令", data["error"])

    def test_legacy_vendor_paths_are_gone(self):
        for path in ("/api/goform/goform_get_cmd_process?cmd=loginfo",
                     "/api/goform/goform_set_cmd_process"):
            status, data = self.get_json(path)
            self.assertEqual(status, 404, path)

    def test_status_fields_come_from_the_local_device(self):
        cmds = ("ppp_status,battery_value,monthly_rx_bytes,lan_ipaddr,wifi_access_sta_num,"
                "cr_version,network_signalbar,sms_unread_num,usb_port_switch,sim_slot,loginfo")
        status, data = self.get_json(
            "/api/ui/fields?multi_data=1&cmd=" + cmds.replace(",", "%2C"))
        self.assertEqual(status, 200)
        self.assertIn(data["ppp_status"], ("ppp_connected", "ppp_disconnected"))
        self.assertTrue(data["cr_version"])
        self.assertEqual(data["sms_unread_num"], "0")
        self.assertEqual(data["sim_slot"], "0")
        # loginfo means "the request is authenticated", which it is here.
        self.assertEqual(data["loginfo"], "ok")
        self.assertTrue(str(data["battery_value"]).lstrip("-").isdigit())

    def test_access_point_list_shape(self):
        status, data = self.get_json(
            "/api/ui/fields?cmd=queryWiFiModuleSwitch,queryAccessPointInfo")
        self.assertEqual(status, 200)
        self.assertIn(data["queryWiFiModuleSwitch"], ("0", "1"))
        self.assertEqual(len(data["queryAccessPointInfo"]), 1)
        item = data["queryAccessPointInfo"][0]
        for key in ("SSID", "Password", "AuthMode", "ChipIndex", "AccessPointIndex",
                    "ApMaxStationNumber", "ApBroadcastDisabled", "AccessPointSwitchStatus",
                    "QrImageUrl"):
            self.assertIn(key, item, key)
        self.assertEqual(item["SSID"], "E5-Test")

    def test_access_control_list_shape(self):
        status, data = self.get_json("/api/ui/fields?cmd=queryDeviceAccessControlList")
        self.assertEqual(status, 200)
        for key in ("AclMode", "BlackMacList", "BlackNameList", "devices"):
            self.assertIn(key, data["queryDeviceAccessControlList"], key)

    def test_action_updates_local_state(self):
        status, data = self.action("DATA_LIMIT_SETTING",
                                   data_volume_limit_switch="1",
                                   data_volume_limit_size="1073741824",
                                   data_volume_alert_percent="90")
        self.assertEqual(status, 200)
        self.assertEqual(data["result"], "success")
        self.assertEqual(self.app.config.get("kano_data_flow_max_limit"), 1073741824)
        self.assertEqual(self.app.config.get("kano_data_flow_alert_percent"), "90")

    def test_action_routes_ap_configuration(self):
        status, _ = self.action("setAccessPointInfo", SSID="FromUI", Password="abcdefgh",
                                ApMaxStationNumber="5", ApBroadcastDisabled="0")
        self.assertEqual(status, 200)
        _, hotspot = self.get_json("/api/linux/hotspot")
        self.assertEqual(hotspot["ssid"], "FromUI")
        self.assertEqual(hotspot["max_clients"], "5")
        self.assertTrue(hotspot["hidden"])

    def test_action_reports_unsupported_actions(self):
        status, data = self.action("LTE_BAND_LOCK", lte_band_lock="1,3")
        self.assertEqual(status, 500)
        self.assertIn("锁频", data["error"])

    def test_action_rejects_unknown_actions(self):
        status, data = self.action("NOT_A_THING")
        self.assertEqual(status, 500)
        self.assertIn("不支持", data["error"])

    def test_action_requires_the_action_key(self):
        status, data = self.action("")
        self.assertEqual(status, 500)
        self.assertIn("action", data["error"])

    # -- proxies -----------------------------------------------------------
    def test_any_proxy_refuses_loopback(self):
        status, data = self.get_json("/api/proxy/--http://127.0.0.1:%d/" % self.port)
        self.assertEqual(status, 403)
        self.assertIn("error", data)

    # -- tasks -------------------------------------------------------------
    def test_task_lifecycle(self):
        status, _ = self.post_json("/api/add_task", {
            "id": "nightly", "time": "03:00:00", "repeatDaily": True,
            "action": {"kind": "command", "command": "true"},
        })
        self.assertEqual(status, 200)
        _, tasks = self.get_json("/api/list_tasks")
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["time"], "03:00")
        self.assertEqual(tasks[0]["actionMap"]["kind"], "command")
        self.assertEqual(self.get_json("/api/get_task?id=nightly")[1]["id"], "nightly")
        self.assertEqual(self.post_json("/api/remove_task", {"id": "nightly"})[1]["result"], "removed")
        self.assertEqual(self.get_json("/api/get_task?id=nightly")[0], 404)

    def test_task_validation(self):
        status, data = self.post_json("/api/add_task", {"id": "", "time": "03:00", "action": {}})
        self.assertEqual(status, 500)
        self.assertIn("error", data)

    def test_task_command_action_runs(self):
        from ufitools.tasks import run_task

        task = {"id": "t", "time": "03:00", "repeatDaily": True,
                "actionMap": {"kind": "command", "command": "echo scheduled-ok"}}
        outcome = run_task(self.app, task)
        self.assertTrue(outcome["ok"])
        self.assertIn("scheduled-ok", outcome["detail"])

    def test_task_forward_without_a_channel_fails_cleanly(self):
        from ufitools.tasks import run_task

        task = {"id": "t", "time": "03:00", "actionMap": {"kind": "forward"}}
        outcome = run_task(self.app, task)
        self.assertFalse(outcome["ok"])

    def test_task_rejects_vendor_style_actions(self):
        """A task action is command or forward -- nothing else is guessed at."""
        from ufitools.tasks import run_task

        task = {"id": "t", "time": "03:00", "actionMap": {"goformId": "REBOOT_DEVICE"}}
        outcome = run_task(self.app, task)
        self.assertFalse(outcome["ok"])
        self.assertIn("缺少 command", outcome["detail"])

    def test_task_rejects_unknown_kind(self):
        from ufitools.tasks import run_task

        task = {"id": "t", "time": "03:00", "actionMap": {"kind": "vendor-thing"}}
        outcome = run_task(self.app, task)
        self.assertFalse(outcome["ok"])
        self.assertIn("未知的 kind", outcome["detail"])

    # -- uploads and plugins ----------------------------------------------
    def test_upload_and_fetch(self):
        boundary = "TESTBOUND"
        body = (
            ("--%s\r\n" % boundary).encode()
            + b'Content-Disposition: form-data; name="file"; filename="pic.png"\r\n'
            + b"Content-Type: image/png\r\n\r\n"
            + b"PNGDATA\r\n"
            + ("--%s--\r\n" % boundary).encode()
        )
        status, payload = self.request(
            "POST", "/api/upload_img", body=body,
            headers={"Content-Type": "multipart/form-data; boundary=%s" % boundary})
        self.assertEqual(status, 200)
        url = json.loads(payload)["url"]
        status, content = self.request("GET", "/api" + url, sign=False)
        self.assertEqual(status, 200)
        self.assertEqual(content, b"PNGDATA")

    def test_plugin_round_trip(self):
        self.assertEqual(self.post_json("/api/set_custom_head", {"text": "console.log(1)"})[0], 200)
        self.assertEqual(self.get_json("/api/get_custom_head", sign=False)[1]["text"],
                         "console.log(1)")

    def test_speedtest_streams_the_requested_size(self):
        status, body = self.request("GET", "/api/speedtest?ckSize=1")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), 8 * 1024 * 1024)

    def test_qr_placeholder_is_served(self):
        # The frontend fetches the QR image with its normal headers, so this is
        # an authenticated endpoint.
        status, body = self.request("GET", "/api/linux/placeholder.svg")
        self.assertEqual(status, 200)
        self.assertIn(b"<svg", body)

    def test_forwarding_requires_a_channel(self):
        status, data = self.post_json("/api/do_forward_msg", {"address": "10086", "body": "hi"})
        self.assertEqual(status, 500)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
