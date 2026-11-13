"""ERS_Main 잡 흐름 통합 테스트 (장치 없이).

릴레이·ADC·UDP 는 가짜, 전원은 실제 ERS_Power_Control 코드에 가짜 DSP 통신만 끼운다.
실행: python -m unittest discover -s DeviceTest
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dsp_modbus  # noqa: E402
import ERS_Main  # noqa: E402
import ERS_Power_Control  # noqa: E402


class FakeDsp:
    """DspModbusClient 대체. 레지스터 동작만 흉내 낸다."""

    def __init__(self, *args, **kwargs):
        self.lock = threading.Lock()
        self.state = dsp_modbus.STATE_STOP
        self.trip = 0
        self.settings = dsp_modbus.Settings(1.0, 0.1, 1.5, 10, 0)
        self.reach_voltage = True
        self.trip_after_run_reads = None   # RUN 중 n 번째 읽기에서 TRIP
        self.run_reads = 0
        self.calls = []

    def _m(self):
        v = self.settings.voltage if (self.state == 1 and self.reach_voltage) else 0.0
        return dsp_modbus.Measurements(0.0, v, 0.0, self.state, self.trip,
                                       1 if self.trip == 3 else 0, 0, 12.0, 12.0, 0.8 if self.trip else 0.0)

    def read_measurements(self):
        with self.lock:
            if self.state == 1:
                self.run_reads += 1
                if self.trip_after_run_reads and self.run_reads >= self.trip_after_run_reads:
                    self.state, self.trip = dsp_modbus.STATE_TRIP, 3
            return self._m()

    def read_settings(self):
        with self.lock:
            s = self.settings
            return dsp_modbus.Settings(s.voltage, s.current, s.oc, s.seconds, int(self.state == 1))

    def write_settings(self, voltage, current, oc, seconds):
        with self.lock:
            self.calls.append(("settings", voltage, current, oc, seconds))
            if self.state != 0:
                raise dsp_modbus.DspException(16, 4)
            self.settings = dsp_modbus.Settings(voltage, current, oc, seconds, 0)

    def write_run(self, run):
        with self.lock:
            self.calls.append(("run", bool(run)))
            if not run:
                if self.state == 1:
                    self.state = 0
                return
            if self.state != 0 or self.trip:
                raise dsp_modbus.DspException(6, 4)
            self.state, self.run_reads = 1, 0

    def write_trip_reset(self):
        with self.lock:
            self.calls.append(("trip_reset",))
            if self.state == 1:
                raise dsp_modbus.DspException(6, 4)
            self.state, self.trip = 0, 0

    def close(self):
        pass


class FakeRelay:
    log = None

    def __init__(self, **kwargs):
        pass

    def test_clear(self):
        return True

    def clear(self):
        FakeRelay.log.append("relay_clear")
        return True

    def set(self, data):
        return True


class FakeADC:
    def __init__(self, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def capture(self, output_file, **kwargs):
        open(output_file, "wb").close()
        return 100


class FakeUDP:
    def __init__(self, **kwargs):
        self.sent = []

    def recv(self):
        return None

    def send(self, data):
        self.sent.append(data.decode())

    def close(self):
        pass


class MainFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        with open(os.path.join(ROOT, "settings.json"), encoding="utf-8") as f:
            settings = json.load(f)
        settings["logger"].update({"file": False, "console": False, "level": "WARNING"})
        settings["main"]["job_path"] = self.tmp
        settings["power"].update({"keepalive_interval_sec": 0.05, "voltage_settle_timeout_sec": 0.3})
        with open(os.path.join(self.tmp, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(settings, f)

        with open(os.path.join(ROOT, "JSON", "DCworkControl.json"), encoding="utf-8") as f:
            job = json.load(f)
        job["DataDir"] = os.path.join(self.tmp, "DataDC")
        job["NoCmd"] = 3
        job["Cmds"] = job["Cmds"][:3]
        job["OnTime"], job["OffTime"], job["NoStack"] = 10, 10, 1
        self.job = job
        self.write_job()

        FakeRelay.log = []
        self.dsp = FakeDsp()
        self.patches = [
            mock.patch.object(ERS_Power_Control, "DspModbusClient", lambda *a, **k: self.dsp),
            mock.patch.object(ERS_Main, "relay_board_controller", FakeRelay),
            mock.patch.object(ERS_Main, "adc_controller", FakeADC),
            mock.patch.object(ERS_Main, "myUDP", FakeUDP),
            mock.patch.object(ERS_Main.ERSMainApp, "missing_devices", lambda self: []),
            mock.patch.object(ERS_Main, "RELAY_POWER_SETTLE_SEC", 0.0),
            mock.patch.object(ERS_Main, "RELAY_SWITCH_SETTLE_SEC", 0.0),
        ]
        for p in self.patches:
            p.start()
        orig_stop = ERS_Power_Control.power_controller.stop

        def stop_spy(power_self):
            FakeRelay.log.append("power_stop")
            return orig_stop(power_self)
        self.patches.append(mock.patch.object(ERS_Power_Control.power_controller, "stop", stop_spy))
        self.patches[-1].start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_job(self):
        with open(os.path.join(self.tmp, "DCworkControl.json"), "w", encoding="utf-8") as f:
            json.dump(self.job, f)

    def run_app(self, app, commands=(), timeout=10.0):
        async def scenario():
            worker = asyncio.create_task(app.worker())
            for cmd in commands:
                if cmd == "DCSTART":
                    app.cmdStart("DC")
                elif cmd == "RESET":
                    app.handle_reset()
                    await asyncio.sleep(0.3)
                    continue
                deadline = asyncio.get_running_loop().time() + timeout
                await asyncio.sleep(0.2)
                while app.device_state == "BUSY" and asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.05)
            app.stop_event.set()
            await worker
        asyncio.run(scenario())

    def make_app(self):
        app = ERS_Main.ERSMainApp(self.tmp)
        return app

    # ------------------------------------------------------------------

    def test_normal_job_done(self):
        app = self.make_app()
        self.assertEqual(app.device_state, "READY")
        self.run_app(app, ["DCSTART"])
        sent = app.udp.sent
        self.assertIn("DCSTARTOK", sent)
        self.assertIn("DCDONE", sent)
        self.assertEqual(app.device_state, "READY")
        settings_call = [c for c in self.dsp.calls if c[0] == "settings"][0]
        self.assertEqual(settings_call[1:4], (20.0, 0.5, 0.75))
        self.assertGreaterEqual(settings_call[4], 1)
        self.assertEqual(self.dsp.state, 0)
        # 전원을 먼저 끄고 릴레이를 해제한다
        self.assertLess(FakeRelay.log.index("power_stop"), FakeRelay.log.index("relay_clear"))
        app.close()

    def test_current_clamped(self):
        self.job["MaxIval"] = 2000
        self.write_job()
        app = self.make_app()
        self.run_app(app, ["DCSTART"])
        settings_call = [c for c in self.dsp.calls if c[0] == "settings"][0]
        self.assertEqual(settings_call[2:4], (1.0, 1.5))
        self.assertIn("DCDONE", app.udp.sent)
        app.close()

    def test_voltage_not_reached_faults(self):
        self.dsp.reach_voltage = False
        app = self.make_app()
        self.run_app(app, ["DCSTART"])
        self.assertIn("FAIL", app.udp.sent)
        self.assertIn("POWER SETUP FAIL", app.udp.sent)
        self.assertEqual(app.device_state, "FAULT")
        self.assertEqual(self.dsp.state, 0)
        app.close()

    def test_trip_during_job_then_reset(self):
        self.dsp.trip_after_run_reads = 3
        app = self.make_app()
        self.run_app(app, ["DCSTART"])
        self.assertEqual(app.device_state, "FAULT")
        self.assertTrue(any(m.startswith("POWER TRIP: cause=OVER_CURRENT") for m in app.udp.sent), app.udp.sent)
        self.assertEqual(self.dsp.state, dsp_modbus.STATE_TRIP)
        self.assertNotIn(("trip_reset",), self.dsp.calls)   # 자동 해제 금지

        self.dsp.trip_after_run_reads = None
        self.run_app(app, ["RESET"])
        self.assertIn(("trip_reset",), self.dsp.calls)
        self.assertIn("RESETOK", app.udp.sent)
        self.assertEqual(app.device_state, "READY")
        app.close()

    def test_init_with_trip_is_fault_and_reset_recovers(self):
        self.dsp.state, self.dsp.trip = dsp_modbus.STATE_TRIP, 3
        app = self.make_app()
        self.assertEqual(app.device_state, "FAULT")
        self.run_app(app, ["RESET"])
        self.assertEqual(app.device_state, "READY")
        self.assertIn("RESETOK", app.udp.sent)
        app.close()

    def test_start_rejected_when_not_ready(self):
        self.dsp.state, self.dsp.trip = dsp_modbus.STATE_TRIP, 3
        app = self.make_app()
        self.run_app(app, ["DCSTART"])
        self.assertIn("DCSTARTFAIL", app.udp.sent)
        self.assertNotIn(("run", True), self.dsp.calls)
        app.close()


if __name__ == "__main__":
    unittest.main()
