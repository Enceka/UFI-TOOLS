"""Traffic accounting tests (day buckets, rate, calibration)."""

from __future__ import annotations

import os
import tempfile
import time
import types
import unittest
from unittest import mock

from ufitools import traffic
from ufitools.config import Config
from ufitools.store import JsonFile


class TrafficTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        config = Config(data_dir=os.path.join(self._tmp.name, "data"))
        config.set("traffic_interfaces", "test0")
        self.app = types.SimpleNamespace(
            config=config,
            runtime=JsonFile(os.path.join(self._tmp.name, "runtime.json"), {}),
        )
        self.counters = {"rx": 1000, "tx": 500}

    def tearDown(self):
        self._tmp.cleanup()

    def _read(self, interface, direction):
        return self.counters[direction]

    def test_first_sample_only_establishes_a_baseline(self):
        with mock.patch.object(traffic, "_read_counter", self._read):
            traffic.sample(self.app)
        self.assertEqual(traffic.today_bytes(self.app), 0)

    def test_delta_is_bucketed_by_day(self):
        with mock.patch.object(traffic, "_read_counter", self._read):
            traffic.sample(self.app)
            self.counters["rx"] += 1500
            self.counters["tx"] += 500
            traffic.sample(self.app)
        self.assertEqual(traffic.today_bytes(self.app), 2000)
        self.assertEqual(traffic.month_bytes(self.app), 2000)

    def test_rate_is_bytes_per_second(self):
        with mock.patch.object(traffic, "_read_counter", self._read):
            traffic.sample(self.app)
            self.app.runtime.data["traffic_previous_at"] = time.time() - 2.0
            self.counters["rx"] += 400
            self.counters["tx"] += 200
            traffic.sample(self.app)
        measured = traffic.rate(self.app)
        self.assertGreater(measured["rx_bps"], 100)
        self.assertLess(measured["rx_bps"], 400)

    def test_counter_reset_is_treated_as_a_reboot(self):
        with mock.patch.object(traffic, "_read_counter", self._read):
            traffic.sample(self.app)
            self.counters["rx"] = 10
            self.counters["tx"] = 5
            traffic.sample(self.app)
        self.assertEqual(traffic.today_bytes(self.app), 0)

    def test_calibration_overrides_the_bucket(self):
        traffic.calibrate(self.app, 123456)
        self.assertEqual(traffic.today_bytes(self.app), 123456)
        traffic.calibrate(self.app, -5)
        self.assertEqual(traffic.today_bytes(self.app), 0)

    def test_range_queries(self):
        now = int(time.time() * 1000)
        traffic.calibrate(self.app, 777)
        self.assertEqual(traffic.range_bytes(self.app, now - 86400000, now + 86400000), 777)
        daily = traffic.range_daily(self.app, now - 86400000, now + 86400000)
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["usage"], "777")

    def test_detect_interfaces_prefers_configuration(self):
        self.assertEqual(traffic.detect_interfaces("eth0,wlan0"), ["eth0", "wlan0"])

    def _route_table(self, *rows):
        header = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
        body = "".join("\t".join(r) + "\n" for r in rows)
        return mock.patch("builtins.open", mock.mock_open(read_data=header + body))

    def test_a_point_to_point_default_route_is_the_uplink(self):
        # "default dev sipa_eth0": no gateway, flags RTF_UP only
        rows = [("br0", "0009A8C0", "00000000", "0001", "0", "0", "0", "00FFFFFF", "0", "0", "0"),
                ("sipa_eth0", "00000000", "00000000", "0001", "0", "0", "100", "00000000", "0", "0", "0")]
        with self._route_table(*rows):
            self.assertEqual(traffic.default_route_interface(), "sipa_eth0")

    def test_the_lowest_metric_default_route_wins(self):
        rows = [("wwan0", "00000000", "00000000", "0001", "0", "0", "600", "00000000", "0", "0", "0"),
                ("eth0", "00000000", "0101A8C0", "0003", "0", "0", "100", "00000000", "0", "0", "0")]
        with self._route_table(*rows):
            self.assertEqual(traffic.default_route_interface(), "eth0")


if __name__ == "__main__":
    unittest.main()
