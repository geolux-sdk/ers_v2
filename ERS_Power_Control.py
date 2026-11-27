"""ERS 2027Version POWER 보드(ESR_DSP) 제어.

통신은 dsp_modbus.py, 규격과 결정 사항은 docs/power-board-dsp-modbus.md (6절, 10.1절).

사용 순서 (잡 하나):
    plan = power.plan(voltage_v, current_ma, seconds)   # 지령 계산 (전류 1A 클램프, OC = 전류 x 1.5)
    power.configure(plan)                               # STOP 상태에서 FC10 쓰기 + FC03 확인
    power.start()                                       # RUN + 통신 유지 스레드 시작
    power.wait_voltage()                                # 출력 전압 도달 확인 (기본 2초)
    ... 측정 중 power.check_running() 으로 TRIP / 예상치 못한 STOP / 통신 두절 확인 ...
    power.stop()

오류 분류:
    PowerTripError     TRIP(과전류 등). 장비 이상 → FAULT. 자동 해제 금지, HOST RESET 으로만 trip_reset().
    PowerCommError     통신 두절(연속 실패). → FAULT.
    PowerStoppedError  RUN 중 DSP 가 스스로 STOP(제한 시간, 10초 무통신). 장비 정상 → 잡 실패, READY.
    PowerRejectedError DSP 가 상태 때문에 거부(예외 04). 자동 재시도 금지.
    PowerSetupError    설정 확인 불일치, 전압 미도달, 지령 범위 오류. → 잡 실패.
"""

import argparse
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from dsp_modbus import (
    STATE_RUN,
    STATE_STOP,
    STATE_TRIP,
    DspCommError,
    DspException,
    DspModbusClient,
    Measurements,
)

MAX_RUN_SECONDS = 9999


class PowerError(Exception):
    pass


class PowerCommError(PowerError):
    pass


class PowerTripError(PowerError):
    def __init__(self, measurements):
        self.measurements = measurements
        super().__init__("POWER TRIP: cause={} peak={:.4f}A ({})".format(
            measurements.trip_name, measurements.peak, measurements.summary()))


class PowerStoppedError(PowerError):
    def __init__(self, measurements):
        self.measurements = measurements
        super().__init__("POWER stopped unexpectedly: {}".format(measurements.summary()))


class PowerRejectedError(PowerError):
    pass


class PowerSetupError(PowerError):
    pass


@dataclass(frozen=True)
class PowerPlan:
    voltage: float           # V
    current: float           # A (클램프 후)
    oc: float                # A
    seconds: int             # 운전 제한 시간
    requested_current_ma: float
    current_clamped: bool


class power_controller:
    def __init__(
        self,
        comport: str = "/dev/ttyAMA1",
        address: int = 1,
        min_voltage_v: float = 1.0,
        max_voltage_v: float = 300.0,
        min_current_a: float = 0.1,
        max_current_a: float = 1.0,
        oc_ratio: float = 1.5,
        oc_min_a: float = 0.3,
        oc_max_a: float = 1.5,
        voltage_tolerance_v: float = 1.0,
        voltage_tolerance_pct: float = 2.0,
        voltage_settle_timeout_sec: float = 2.0,
        voltage_poll_interval_sec: float = 0.2,
        keepalive_interval_sec: float = 1.0,
        keepalive_fail_limit: int = 3,
        test_voltage_v: float = 30.0,
        run_time_margin_ratio: float = 1.5,
        logger: Optional[logging.Logger] = None,
    ):
        self.logger = logger or logging.getLogger(__name__)
        self.min_voltage_v = min_voltage_v
        self.max_voltage_v = max_voltage_v
        self.min_current_a = min_current_a
        self.max_current_a = max_current_a
        self.oc_ratio = oc_ratio
        self.oc_min_a = oc_min_a
        self.oc_max_a = oc_max_a
        self.voltage_tolerance_v = voltage_tolerance_v
        self.voltage_tolerance_pct = voltage_tolerance_pct
        self.voltage_settle_timeout_sec = voltage_settle_timeout_sec
        self.voltage_poll_interval_sec = voltage_poll_interval_sec
        self.keepalive_interval_sec = keepalive_interval_sec
        self.keepalive_fail_limit = keepalive_fail_limit
        self.test_voltage_v = test_voltage_v
        self.run_time_margin_ratio = run_time_margin_ratio

        self.plan_active: Optional[PowerPlan] = None
        self._keepalive_thread: Optional[threading.Thread] = None
        self._keepalive_stop = threading.Event()
        self._state_lock = threading.Lock()
        self._latest: Optional[Measurements] = None
        self._keepalive_failures = 0
        self._keepalive_error: Optional[str] = None

        self.client = DspModbusClient(comport, address, logger=self.logger)

    # ------------------------------------------------------------------
    # 기본
    # ------------------------------------------------------------------

    def close(self):
        self._stop_keepalive()
        self.client.close()

    def read(self) -> Measurements:
        try:
            m = self.client.read_measurements()
        except DspCommError as err:
            raise PowerCommError(str(err)) from err
        with self._state_lock:
            self._latest = m
        return m

    def check(self) -> Measurements:
        """초기화 확인: 통신, 상태, 설정을 읽는다. TRIP 이면 PowerTripError. RUN 중이면 STOP 시킨다."""
        m = self.read()
        try:
            s = self.client.read_settings()
        except DspCommError as err:
            raise PowerCommError(str(err)) from err
        self.logger.info("POWER check: %s", m.summary())
        self.logger.info("POWER settings: %s", s)
        if m.state == STATE_TRIP:
            raise PowerTripError(m)
        if m.state == STATE_RUN:
            self.logger.warning("POWER was RUN at check, sending STOP")
            self.stop()
            m = self.read()
        return m

    # ------------------------------------------------------------------
    # 지령 계산 (순수 함수 성격, 장치 접근 없음)
    # ------------------------------------------------------------------

    def plan(self, voltage_v: float, current_ma: float, seconds: float) -> PowerPlan:
        voltage = float(voltage_v)
        if not self.min_voltage_v <= voltage <= self.max_voltage_v:
            raise PowerSetupError("voltage {}V out of range {}~{}V".format(
                voltage, self.min_voltage_v, self.max_voltage_v))

        requested = float(current_ma)
        current = requested / 1000.0
        clamped = False
        if current > self.max_current_a:
            self.logger.warning("POWER current %.0fmA exceeds %.3fA, clamped",
                                requested, self.max_current_a)
            current, clamped = self.max_current_a, True
        elif current < self.min_current_a:
            self.logger.warning("POWER current %.0fmA below %.3fA, raised",
                                requested, self.min_current_a)
            current, clamped = self.min_current_a, True

        oc = min(max(current * self.oc_ratio, self.oc_min_a), self.oc_max_a)
        secs = int(min(max(round(seconds), 1), MAX_RUN_SECONDS))
        return PowerPlan(voltage, round(current, 4), round(oc, 4), secs, requested, clamped)

    def run_seconds(self, per_command_sec: float, command_count: int, fixed_sec: float = 0.0) -> int:
        """운전 제한 시간 = (명령당 최대 시간 x 명령 수 + 고정 시간) x 여유 배율, 1~9999초."""
        total = (per_command_sec * command_count + fixed_sec) * self.run_time_margin_ratio
        return int(min(max(round(total), 1), MAX_RUN_SECONDS))

    def voltage_tolerance(self, target: float) -> float:
        return max(self.voltage_tolerance_v, abs(target) * self.voltage_tolerance_pct / 100.0)

    # ------------------------------------------------------------------
    # 운전
    # ------------------------------------------------------------------

    def configure(self, plan: PowerPlan) -> None:
        """STOP 상태에서 설정을 쓰고 FC03 으로 확인. 자동 재시도하지 않는다."""
        self.logger.info("POWER configure: %s", plan)
        try:
            self.client.write_settings(plan.voltage, plan.current, plan.oc, plan.seconds)
            s = self.client.read_settings()
        except DspException as err:
            if err.code == 4:
                raise PowerRejectedError("configure rejected (not STOP or local menu busy)") from err
            raise PowerSetupError(str(err)) from err
        except DspCommError as err:
            raise PowerCommError(str(err)) from err

        def close(a, b):
            return abs(a - b) <= 1e-4 * max(1.0, abs(b))

        if not (close(s.voltage, plan.voltage) and close(s.current, plan.current)
                and close(s.oc, plan.oc) and s.seconds == plan.seconds):
            raise PowerSetupError("settings readback mismatch: wrote {} read {}".format(plan, s))
        self.plan_active = plan

    def start(self) -> Measurements:
        """RUN 명령 후 상태 확인, 통신 유지 스레드 시작."""
        try:
            self.client.write_run(True)
        except DspException as err:
            if err.code == 4:
                m = self.read()
                if m.state == STATE_TRIP:
                    raise PowerTripError(m) from err
                raise PowerRejectedError("RUN rejected: {}".format(m.summary())) from err
            raise PowerSetupError(str(err)) from err
        except DspCommError as err:
            raise PowerCommError(str(err)) from err

        m = self.read()
        if m.state != STATE_RUN:
            raise PowerSetupError("RUN not entered: {}".format(m.summary()))
        self.logger.info("POWER RUN: %s", m.summary())
        self._start_keepalive()
        return m

    def wait_voltage(self, target: Optional[float] = None, timeout: Optional[float] = None) -> Measurements:
        """출력 전압이 허용 범위에 들어올 때까지 기다린다. 실패하면 PowerSetupError (STOP 은 호출 측 책임)."""
        if target is None:
            if self.plan_active is None:
                raise PowerSetupError("no active plan")
            target = self.plan_active.voltage
        timeout = self.voltage_settle_timeout_sec if timeout is None else timeout
        tol = self.voltage_tolerance(target)
        deadline = time.monotonic() + timeout
        while True:
            m = self.read()
            self._raise_if_not_running(m)
            if abs(m.voltage - target) <= tol:
                self.logger.info("POWER voltage reached: target=%.3fV measured=%.3fV tol=%.3fV",
                                 target, m.voltage, tol)
                return m
            if time.monotonic() >= deadline:
                raise PowerSetupError("voltage not reached in {:.1f}s: target={:.3f}V measured={:.3f}V tol={:.3f}V".format(
                    timeout, target, m.voltage, tol))
            time.sleep(self.voltage_poll_interval_sec)

    def check_running(self) -> Measurements:
        """측정 중 호출. 통신 유지 스레드의 최신 측정으로 TRIP / STOP / 통신 두절을 판단한다."""
        with self._state_lock:
            m = self._latest
            failures = self._keepalive_failures
            error = self._keepalive_error
        if failures >= self.keepalive_fail_limit:
            raise PowerCommError("keepalive failed {} times: {}".format(failures, error))
        if m is None:
            m = self.read()
        self._raise_if_not_running(m)
        return m

    def stop(self) -> Measurements:
        """STOP 명령. 상태와 관계없이 허용된다. 통신 유지 스레드도 멈춘다."""
        self._stop_keepalive()
        try:
            self.client.write_run(False)
        except (DspCommError, DspException) as err:
            raise PowerCommError("STOP failed: {!r}".format(err)) from err
        m = self.read()
        self.logger.info("POWER STOP: %s", m.summary())
        if m.state == STATE_RUN:
            raise PowerCommError("still RUN after STOP: {}".format(m.summary()))
        self.plan_active = None
        return m

    def trip_reset(self) -> Measurements:
        """HOST RESET 명령에서만 호출. 해제 전 트립 정보를 로그로 남긴다."""
        before = self.read()
        self.logger.warning("POWER trip reset requested, before: %s", before.summary())
        try:
            self.client.write_trip_reset()
        except DspException as err:
            if err.code == 4:
                raise PowerRejectedError("trip reset rejected: {}".format(before.summary())) from err
            raise PowerSetupError(str(err)) from err
        except DspCommError as err:
            raise PowerCommError(str(err)) from err
        after = self.read()
        self.logger.info("POWER trip reset, after: %s", after.summary())
        if after.state != STATE_STOP:
            raise PowerSetupError("not STOP after trip reset: {}".format(after.summary()))
        return after

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_if_not_running(m: Measurements) -> None:
        if m.state == STATE_TRIP:
            raise PowerTripError(m)
        if m.state != STATE_RUN:
            raise PowerStoppedError(m)

    def _start_keepalive(self):
        self._stop_keepalive()
        with self._state_lock:
            self._keepalive_failures = 0
            self._keepalive_error = None
        self._keepalive_stop.clear()
        self._keepalive_thread = threading.Thread(target=self._keepalive_loop,
                                                  name="power-keepalive", daemon=True)
        self._keepalive_thread.start()

    def _stop_keepalive(self):
        thread = self._keepalive_thread
        if thread is not None:
            self._keepalive_stop.set()
            thread.join(timeout=2.0)
            self._keepalive_thread = None

    def _keepalive_loop(self):
        """RUN 중 주기적으로 FC04 를 읽어 DSP 10초 무통신 STOP 을 막고, 최신 상태를 보관한다."""
        while not self._keepalive_stop.wait(self.keepalive_interval_sec):
            try:
                m = self.client.read_measurements()
            except (DspCommError, DspException) as err:
                with self._state_lock:
                    self._keepalive_failures += 1
                    self._keepalive_error = repr(err)
                    failures = self._keepalive_failures
                self.logger.warning("POWER keepalive failed (%d): %r", failures, err)
                continue
            with self._state_lock:
                self._latest = m
                self._keepalive_failures = 0
                self._keepalive_error = None
            if m.state != STATE_RUN:
                self.logger.error("POWER left RUN: %s", m.summary())
                return  # check_running() 이 최신 상태로 판단한다


# ----------------------------------------------------------------------
# 단독 실행 (장치 확인용)
# ----------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description="ERS POWER (ESR_DSP) controller")
    p.add_argument("--port", default="/dev/ttyAMA1")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("run", help="configure + RUN + keepalive, then STOP")
    r.add_argument("voltage", type=float)
    r.add_argument("current_ma", type=float)
    r.add_argument("--hold", type=float, default=15.0, help="RUN 유지 시간(s)")
    r.add_argument("--no-wait", action="store_true", help="전압 도달 판정 생략")
    sub.add_parser("reset", help="TRIP 해제")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    power = power_controller(comport=args.port)
    try:
        if args.cmd == "check":
            power.check()
        elif args.cmd == "reset":
            power.trip_reset()
        else:
            power.check()
            plan = power.plan(args.voltage, args.current_ma, args.hold + 30)
            power.configure(plan)
            power.start()
            try:
                if not args.no_wait:
                    power.wait_voltage()
                end = time.monotonic() + args.hold
                while time.monotonic() < end:
                    m = power.check_running()
                    logging.info("running: %s", m.summary())
                    time.sleep(3.0)
            finally:
                power.stop()
    except PowerError as err:
        logging.error("%s: %s", type(err).__name__, err)
        return 1
    finally:
        power.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
