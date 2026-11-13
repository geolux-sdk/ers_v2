"""장치 없이 도는 POWER 제어 단위 테스트.  실행: python -m unittest discover -s DeviceTest"""
import os
import struct
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dsp_modbus  # noqa: E402
import ERS_Power_Control as pc  # noqa: E402


def make_power(**kw):
    with mock.patch.object(pc, "DspModbusClient"):
        return pc.power_controller(**kw)


def meas(state=1, voltage=30.0, trip=0):
    return dsp_modbus.Measurements(0.0, voltage, 0.5, state, trip, 0, 1, 12.0, 12.0, 0.5)


class FrameTests(unittest.TestCase):
    def test_crc_examples_from_doc(self):
        cases = {
            "01 04 00 00 00 10": "F1 C6",
            "01 03 00 00 00 08": "44 0C",
            "01 06 00 07 00 01": "F9 CB",
            "01 06 00 07 00 00": "38 0B",
            "01 06 00 08 00 01": "C9 C8",
        }
        for body, crc in cases.items():
            self.assertEqual(dsp_modbus.hexs(dsp_modbus.crc16(bytes.fromhex(body))), crc, body)

    def test_settings_payload_doc_example(self):
        data = dsp_modbus.encode_settings(24.0, 0.5, 0.75, 30)
        self.assertEqual(dsp_modbus.hexs(data), "41 C0 00 00 3F 00 00 00 3F 40 00 00 00 1E")

    def test_decode_measurements(self):
        raw = bytes.fromhex("C1CFEDE9C1D0B21E3DEE8EE900000000000000004" "13DDA5D413ED97800000000")
        m = dsp_modbus.decode_measurements(raw)
        self.assertAlmostEqual(m.voltage, -26.087, places=3)
        self.assertEqual((m.state, m.trip, m.elapsed), (0, 0, 0))
        self.assertAlmostEqual(m.vbat, 11.866, places=3)

    def test_decode_settings(self):
        raw = bytes.fromhex("3F8000003DCCCCCD3FC00000000A0000")
        s = dsp_modbus.decode_settings(raw)
        self.assertEqual((s.voltage, s.seconds, s.run), (1.0, 10, 0))
        self.assertAlmostEqual(s.current, 0.1, places=6)

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            dsp_modbus.encode_settings(float("nan"), 0.5, 0.75, 10)


class PlanTests(unittest.TestCase):
    def setUp(self):
        self.p = make_power()

    def test_normal(self):
        plan = self.p.plan(20, 500, 120)
        self.assertEqual((plan.voltage, plan.current, plan.oc, plan.seconds), (20.0, 0.5, 0.75, 120))
        self.assertFalse(plan.current_clamped)

    def test_current_clamped_to_1a(self):
        plan = self.p.plan(20, 2000, 60)
        self.assertEqual((plan.current, plan.oc), (1.0, 1.5))
        self.assertTrue(plan.current_clamped)
        self.assertEqual(plan.requested_current_ma, 2000)

    def test_current_raised_to_min(self):
        plan = self.p.plan(20, 50, 60)
        self.assertEqual((plan.current, plan.oc), (0.1, 0.3))
        self.assertTrue(plan.current_clamped)

    def test_oc_floor(self):
        self.assertEqual(self.p.plan(20, 150, 60).oc, 0.3)    # 0.225 -> 0.3

    def test_voltage_range(self):
        for v in (0, 0.5, 301):
            with self.assertRaises(pc.PowerSetupError):
                self.p.plan(v, 500, 60)

    def test_seconds_bounds(self):
        self.assertEqual(self.p.plan(20, 500, 0).seconds, 1)
        self.assertEqual(self.p.plan(20, 500, 50000).seconds, 9999)

    def test_limits_from_settings(self):
        p = make_power(max_current_a=2.0, oc_max_a=3.0)
        plan = p.plan(20, 2000, 60)
        self.assertEqual((plan.current, plan.oc, plan.current_clamped), (2.0, 3.0, False))

    def test_run_seconds(self):
        self.assertEqual(self.p.run_seconds(10.0, 20, fixed_sec=5.0), 308)   # (200+5)*1.5
        self.assertEqual(self.p.run_seconds(100.0, 1000), 9999)
        self.assertEqual(self.p.run_seconds(0.0, 0), 1)

    def test_settings_json_keys_match_constructor(self):
        import json
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "settings.json"), encoding="utf-8") as f:
            power = json.load(f)["power"]
        make_power(**power)   # 알 수 없는 키가 있으면 TypeError

    def test_voltage_tolerance(self):
        self.assertEqual(self.p.voltage_tolerance(20), 1.0)     # 2% = 0.4 < 1V
        self.assertAlmostEqual(self.p.voltage_tolerance(200), 4.0)


class RunStateTests(unittest.TestCase):
    def setUp(self):
        self.p = make_power()

    def test_trip_raises_fault(self):
        with self.assertRaises(pc.PowerTripError):
            self.p._raise_if_not_running(meas(state=2, trip=3))

    def test_unexpected_stop(self):
        with self.assertRaises(pc.PowerStoppedError):
            self.p._raise_if_not_running(meas(state=0))

    def test_keepalive_failures_become_comm_error(self):
        self.p._latest = meas()
        self.p._keepalive_failures = 3
        with self.assertRaises(pc.PowerCommError):
            self.p.check_running()

    def test_check_running_ok(self):
        self.p._latest = meas()
        self.assertEqual(self.p.check_running().state, 1)

    def test_configure_readback_mismatch(self):
        plan = self.p.plan(20, 500, 60)
        self.p.client.read_settings.return_value = dsp_modbus.Settings(19.0, 0.5, 0.75, 60, 0)
        with self.assertRaises(pc.PowerSetupError):
            self.p.configure(plan)

    def test_configure_rejected_04(self):
        plan = self.p.plan(20, 500, 60)
        self.p.client.write_settings.side_effect = dsp_modbus.DspException(16, 4)
        with self.assertRaises(pc.PowerRejectedError):
            self.p.configure(plan)

    def test_wait_voltage_timeout(self):
        self.p.plan_active = self.p.plan(20, 500, 60)
        self.p.client.read_measurements.return_value = meas(voltage=5.0)
        with self.assertRaises(pc.PowerSetupError):
            self.p.wait_voltage(timeout=0.3)

    def test_wait_voltage_reached(self):
        self.p.plan_active = self.p.plan(20, 500, 60)
        self.p.client.read_measurements.return_value = meas(voltage=19.3)
        self.assertAlmostEqual(self.p.wait_voltage().voltage, 19.3)


if __name__ == "__main__":
    unittest.main()
