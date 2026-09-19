"""Device-info collector tests against a fixture /proc and /sys tree."""

from __future__ import annotations

import os
import tempfile
import unittest

from ufitools.sysinfo import ProcSys, DeviceInfo

STAT_A = """cpu  100 0 100 800 0 0 0 0 0 0
cpu0 50 0 50 400 0 0 0 0 0 0
cpu1 50 0 50 400 0 0 0 0 0 0
intr 0
"""
STAT_B = """cpu  200 0 200 1400 0 0 0 0 0 0
cpu0 100 0 100 700 0 0 0 0 0 0
cpu1 100 0 100 700 0 0 0 0 0 0
intr 0
"""
MEMINFO = """MemTotal:        4000000 kB
MemFree:          900000 kB
MemAvailable:    1000000 kB
SwapTotal:       1000000 kB
SwapFree:         500000 kB
"""


def write(root: str, relative: str, content: str) -> None:
    path = os.path.join(root, relative)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def build_fixture(root: str) -> None:
    write(root, "proc/stat", STAT_A)
    write(root, "proc/meminfo", MEMINFO)
    write(root, "proc/uptime", "12345.67 500.00\n")
    write(root, "proc/loadavg", "0.10 0.20 0.30 1/100 1234\n")
    write(root, "proc/net/tcp", "  sl  local_address rem_address   st\n"
                                 "   0: 0100007F:1F90 00000000:0000 0A 0 0 0 0\n"
                                 "   1: 0100007F:1F91 0100007F:0050 01 0 0 0 0\n")
    write(root, "proc/net/tcp6", "  sl  local_address\n   0: 0000000000000000:1F90 0000000000000000:0000 0A\n")
    write(root, "proc/net/udp", " sl  local_address\n 0: 00000000:0035 00000000:0000 07\n")
    write(root, "proc/net/udp6", " sl  local_address\n 0: 00:0035 00:0000 07\n")
    write(root, "proc/net/unix", "Num       RefCount Protocol Flags\n"
                                  "0000: 00000002 00000000 00010000\n0001: 00000002 00000000 00010000\n")
    write(root, "proc/cpuinfo", "processor\t: 0\nHardware\t: Unisoc T158\n")

    write(root, "sys/class/thermal/thermal_zone0/type", "cpu\n")
    write(root, "sys/class/thermal/thermal_zone0/temp", "45000\n")
    write(root, "sys/class/thermal/thermal_zone1/type", "wcn\n")
    write(root, "sys/class/thermal/thermal_zone1/temp", "99000\n")

    write(root, "sys/class/power_supply/battery/type", "Battery\n")
    write(root, "sys/class/power_supply/battery/capacity", "80\n")
    write(root, "sys/class/power_supply/battery/status", "Charging\n")
    write(root, "sys/class/power_supply/battery/current_now", "-350000\n")
    write(root, "sys/class/power_supply/battery/voltage_now", "3900000\n")

    write(root, "sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq", "1800000\n")
    write(root, "sys/devices/system/cpu/cpu0/cpufreq/cpuinfo_max_freq", "2000000\n")
    write(root, "sys/devices/system/cpu/cpu1/cpufreq/scaling_cur_freq", "2000000\n")
    write(root, "sys/devices/system/cpu/cpu1/cpufreq/cpuinfo_max_freq", "2000000\n")

    write(root, "sys/bus/usb/devices/usb1/product", "xHCI Host Controller\n")
    write(root, "sys/bus/usb/devices/usb1/speed", "480\n")
    write(root, "sys/bus/usb/devices/1-1/product", "USB Storage\n")
    write(root, "sys/bus/usb/devices/1-1/speed", "480\n")


class SysinfoTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        build_fixture(self._tmp.name)
        self.info = DeviceInfo(root=self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_root_prefix(self):
        self.assertEqual(ProcSys(self._tmp.name).path("/proc/stat"),
                         os.path.join(self._tmp.name, "proc/stat"))
        self.assertEqual(ProcSys("/").path("/proc/stat"), "/proc/stat")

    def test_cpu_usage_from_previous_sample(self):
        self.info._prev_cpu = self.info._read_proc_stat()
        write(self._tmp.name, "proc/stat", STAT_B)
        usage, overall = self.info.cpu_usage()
        self.assertEqual(usage["cpu"], "25.0")
        self.assertEqual(overall, 25.0)
        self.assertIn("cpu0", usage)

    def test_memory(self):
        info, percent = self.info.memory()
        self.assertEqual(info["mem_total_kb"], 4000000)
        self.assertEqual(info["mem_used_kb"], 3000000)
        self.assertEqual(info["mem_usage_percent"], "75.0")
        self.assertEqual(info["swap_used_kb"], 500000)
        self.assertEqual(percent, 75.0)

    def test_thermal_filters_wcn_and_takes_max(self):
        max_temp, zones = self.info.thermal()
        self.assertEqual(max_temp, 45000)
        self.assertEqual(zones, [{"type": "cpu", "temp": 45000}])

    def test_battery(self):
        battery = self.info.battery()
        self.assertEqual(battery["percent"], 80)
        self.assertEqual(battery["status"], "Charging")
        self.assertEqual(battery["current_uA"], -350000)
        self.assertEqual(battery["voltage_uV"], 3900000)

    def test_cpu_freq(self):
        freq = self.info.cpu_freq()
        self.assertEqual(freq["cpu0"], {"cur": 1800, "max": 2000})
        self.assertEqual(freq["cpu1"], {"cur": 2000, "max": 2000})

    def test_usb_skips_root_hubs(self):
        max_speed, details = self.info.usb()
        self.assertEqual(max_speed, 480)
        products = [d["product"] for d in details["devices"]]
        self.assertEqual(products, ["USB Storage"])

    def test_conn_counts(self):
        counts = self.info.conn_counts()
        self.assertEqual(counts["tcp"], 2)
        self.assertEqual(counts["tcp_active"], 1)
        self.assertEqual(counts["tcp_other"], 1)
        self.assertEqual(counts["udp"], 1)
        self.assertEqual(counts["unix"], 2)

    def test_uptime_and_model(self):
        self.assertEqual(self.info.uptime(), 12345)
        self.assertEqual(self.info.model(), "Unisoc T158")

    def test_storage_of_a_real_directory(self):
        storage = self.info.storage(self._tmp.name)
        self.assertGreater(storage["total"], 0)
        self.assertGreaterEqual(storage["used"], 0)

    def test_missing_paths_are_tolerated(self):
        empty = DeviceInfo(root=os.path.join(self._tmp.name, "does-not-exist"))
        self.assertEqual(empty.uptime(), -1)
        self.assertEqual(empty.conn_counts()["tcp"], -1)
        self.assertEqual(empty.thermal(), (-1, []))
        self.assertEqual(empty.cpu_freq(), {})


if __name__ == "__main__":
    unittest.main()
