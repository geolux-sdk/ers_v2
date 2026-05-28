import asyncio
import json
import os

from jsonschema import ValidationError

from ERS_ADC_Control import (
    DEFAULT_BUSY_POLL_INTERVAL_SEC,
    DEFAULT_BUSY_TIMEOUT_SEC,
    adc_controller,
)
from ERS_Power_Control import power_controller
from ERS_Relay_Control import relay_board_controller
from mySettings import mySettings
from myUDP import myUDP
from pi_gpio import GPIOController


class ERSMainApp:
    def __init__(self, path):
        self.config = None
        self.logger = None

        self.udp = None
        self.relay = None
        self.power = None
        self.gpio = None
        self.relay_power_enabled = False

        self.jobpath = ""
        self.job = None
        self.mode = ""
        self.fault_message = ""

        self.device_state = "INIT"
        self.stop_event = asyncio.Event()
        self.process_stop_event = asyncio.Event()

        try:
            self.config = mySettings(folder_name=path)
        except (FileNotFoundError, json.JSONDecodeError, ValidationError) as err:
            print(f">> Error loading settings: {repr(err)}", flush=True)
            self.device_state = "ERROR"
            return
        except Exception:
            raise

        self.logger = self.config.get_logger()

        self.console("\n\n--------- Program Started---------------------------------")
        self.set_state("WAIT")

        try:
            self.udp = myUDP(**self.config.settings["udp"], logger=self.logger)
        except (OSError, ConnectionError) as err:
            self.console(f">> Error UDP Connecting: {repr(err)}", level="error")
            self.set_state("ERROR")
            return
        except Exception:
            raise

        self.console(">> UDP OPENED")

        try:
            self.gpio = GPIOController(self.logger)
            self.console(">> GPIO OPENED")
        except Exception as err:
            self.console(f">> GPIO init error: {repr(err)}", level="error")
            self.set_state("FAULT")
            return

        try:
            self.relay = relay_board_controller(
                **self.config.settings["relay"],
                logger=self.logger,
            )
            relay_flag = True
        except Exception as err:
            self.console(f">> Relay init error: {repr(err)}", level="error")
            relay_flag = False

        self.console(f">> RELAY OPENED {relay_flag}")

        try:
            self.power = power_controller(
                **self.config.settings["power"],
                logger=self.logger,
            )

            power_flag = self.power.set_init_values()
            self.power.adjust_current_offset()

        except Exception as err:
            self.console(f">> Power init error: {repr(err)}", level="error")
            power_flag = False

        self.console(f">> POWER OPENED {power_flag}")

        self.jobpath = self.config.settings["main"]["job_path"]

        if not (relay_flag and power_flag):
            self.set_state("FAULT")
            self.console(
                f">> FAULT relay_flag={relay_flag}, power_flag={power_flag}",
                level="error",
            )
            return

        self.set_state("READY")

    def console(self, message, level="info"):
        """
        shell에는 항상 출력하고,
        logger가 준비되어 있으면 같은 내용을 log 파일에도 기록한다.
        """
        print(message, flush=True)

        if self.logger is None:
            return

        try:
            log_func = getattr(self.logger, level, self.logger.info)
            log_func(message)
        except Exception:
            pass

    def close(self):
        """
        프로그램 종료 시 장비를 안전하게 정리한다.
        초기화 실패로 일부 객체가 없을 수 있으므로 None 체크를 사용한다.
        """
        try:
            if self.relay is not None and self.relay_power_enabled:
                relay_clear_ok = self.relay.clear()
                if not relay_clear_ok:
                    self.console(">> Relay clear during close failed: no response", level="warning")
        except Exception as err:
            self.console(f">> Relay clear during close failed: {repr(err)}", level="warning")

        try:
            if self.power is not None:
                self.power.stop()
                self.power.close()
        except Exception as err:
            self.console(f">> Power close failed: {repr(err)}", level="error")

        try:
            if self.gpio is not None:
                self.gpio.close()
        except Exception as err:
            self.console(f">> GPIO close failed: {repr(err)}", level="error")

        try:
            if self.udp is not None:
                self.udp.close()
        except Exception as err:
            self.console(f">> UDP close failed: {repr(err)}", level="error")

        self.console(">> ERS Closed")

    async def host_communicator(self):
        self.console(">> host_communicator started")
        self.console(f">> ERS MAIN STARTED device_state={self.device_state}")

        command_map = {
            "DCSTART": lambda: self.cmdStart("DC"),
            "IPSTART": lambda: self.cmdStart("IP"),
            "SPSTART": lambda: self.cmdStart("SP"),
            "TESTELSTART": lambda: self.cmdTestStart("TESTEL"),
            "TEST": lambda: self.cmdTestStart("TESTEL"),
            "TESTADCSTART": lambda: self.cmdTestStart("TESTADC"),
            "QUIT": self.handle_quit,
        }

        while not self.stop_event.is_set():
            await asyncio.sleep(0.05)

            try:
                recvdata = self.udp.recv()
            except KeyboardInterrupt:
                self.console(">> KeyboardInterrupt by the user.")
                self.stop_event.set()
                break
            except Exception as err:
                self.console(f">> UDP receive error: {repr(err)}", level="error")
                await asyncio.sleep(0.5)
                continue

            if recvdata is None:
                continue

            try:
                cmd = recvdata.decode().strip()
            except UnicodeDecodeError as err:
                self.console(f">> Invalid UDP data decode error: {repr(err)}", level="warning")
                continue

            self.console(
                f">> Received command: cmd={cmd} in device_state={self.device_state}"
            )

            if cmd == "STATUS":
                self.send_msg(self.device_state)
                continue

            if cmd in command_map:
                try:
                    command_map[cmd]()
                    self.logger.info(f"Executed command: {cmd}")
                except Exception as err:
                    self.console(
                        f">> Error executing command {cmd}: {repr(err)}",
                        level="error",
                    )
            else:
                self.console(f">> Unknown command received: {cmd}", level="warning")

        self.console(">> ERS_Main host_communicator terminated")

    def handle_quit(self):
        """
        BUSY 상태에서는 실제 worker가 정리할 수 있도록 stop flag만 올린다.
        여기서 바로 READY로 바꾸면 다음 작업이 겹칠 수 있다.
        """
        if self.device_state == "BUSY":
            self.process_stop_event.set()
            self.console(">> QUIT in BUSY, stopping current job...")
        elif self.device_state == "READY":
            self.console(">> QUIT in READY")
        else:
            self.console(f">> QUIT received during {self.device_state}, stopping main loop")
            self.stop_event.set()

    def set_jobpath(self, path):
        self.console(f">> Set Job path={path}")
        self.jobpath = path

    def set_state(self, state):
        self.device_state = state
        if self.logger:
            self.logger.info(f"Set state={state}")

    def set_ready(self):
        self.set_state("READY")

    def set_wait(self):
        self.job = None
        self.set_state("WAIT")

    def send_msg(self, msg):
        try:
            self.console(f">> UDP send_msg: {msg}")
            if self.udp is not None:
                self.udp.send(f"{msg}".encode())
        except Exception as err:
            self.console(f">> UDP send_msg failed: {repr(err)}", level="error")

    def send_STARTOK(self):
        self.send_msg(f"{self.mode}STARTOK")

    def send_DONE(self):
        self.job = None
        self.send_msg(f"{self.mode}DONE")
        self.set_state("READY")

    def send_FAIL(self):
        self.job = None
        self.send_msg("FAIL")
        self.set_state("FAULT")

    def send_STARTFAIL(self, job_type):
        self.send_msg(f"{job_type}STARTFAIL")

    def cmdTestStart(self, job_type):
        """
        TESTEL / TESTADC 공통 시작 함수.
        두 테스트 모두 DCworkControl.json을 사용한다.
        """
        self.mode = job_type
        self.job = None
        self.fault_message = ""

        if self.device_state != "READY":
            self.console(
                f">> {job_type} START FAIL: device_state={self.device_state}",
                level="warning",
            )
            self.send_STARTFAIL(job_type)
            return

        job = self.load_work_file(
            os.path.join(self.jobpath, "DCworkControl.json"),
            job_type,
        )

        if job is None:
            self.send_STARTFAIL(job_type)
            if self.fault_message:
                self.send_msg(self.fault_message)
            return

        if job.get("TaskType") != "DC":
            self.fault_message = "JOB TASK TYPE MISMATCH"
            self.console(
                f">> job Type {job.get('TaskType')} != DC for {job_type}",
                level="error",
            )
            self.send_STARTFAIL(job_type)
            self.send_msg(self.fault_message)
            return

        self.process_stop_event.clear()
        self.set_state("BUSY")
        self.job = job
        self.send_STARTOK()
        self.console(f">> {job_type} START OK")

    def cmdStart(self, job_type):
        self.mode = job_type
        self.job = None
        self.fault_message = ""

        if self.device_state != "READY":
            self.console(
                f">> {job_type} START FAIL: device_state={self.device_state}",
                level="warning",
            )
            self.send_STARTFAIL(job_type)
            return

        job = self.load_work_file(
            os.path.join(self.jobpath, job_type + "workControl.json"),
            job_type,
        )

        if job is None:
            self.send_STARTFAIL(job_type)
            if self.fault_message:
                self.send_msg(self.fault_message)
            return

        if job.get("TaskType") != job_type:
            self.fault_message = "JOB TASK TYPE MISMATCH"
            self.console(
                f">> job Type {job.get('TaskType')} != {job_type}",
                level="error",
            )
            self.send_STARTFAIL(job_type)
            self.send_msg(self.fault_message)
            return

        self.process_stop_event.clear()
        self.set_state("BUSY")
        self.job = job
        self.send_STARTOK()
        self.console(f">> {job_type} START OK")

    def load_work_file(self, fn, job_type):
        self.console(f">> load job filename {fn}")
        self.fault_message = ""

        try:
            with open(fn, "r", encoding="utf-8") as f:
                job = json.load(f)
        except FileNotFoundError:
            self.fault_message = f"JOB FILE NOT FOUND: {fn}"
            self.console(f">> {self.fault_message}", level="error")
            return None
        except json.JSONDecodeError as err:
            self.fault_message = f"JOB FILE JSON ERROR: {repr(err)}"
            self.console(f">> {self.fault_message}", level="error")
            return None

        self.logger.debug(f"job={job}")

        required_keys = [
            "TaskType",
            "DataDir",
            "FileNameBase",
            "OnTime",
            "OffTime",
            "NoStack",
            "MaxVval",
            "MaxIval",
        ]

        for key in required_keys:
            if key not in job:
                self.fault_message = f"JOB FILE MISSING KEY: {key}"
                self.console(f">> {self.fault_message}", level="error")
                return None

        def cmds_check(cmds, no_cmd, field_name, allow_partial=False):
            if type(no_cmd) is not int or no_cmd < 0:
                self.fault_message = (
                    f"JOB COMMAND COUNT INVALID: {field_name} expected count={no_cmd}"
                )
                self.console(f">> {self.fault_message}", level="error")
                return False

            if not isinstance(cmds, list):
                self.fault_message = f"JOB COMMAND FIELD INVALID: {field_name} is not a list"
                self.console(f">> {self.fault_message}", level="error")
                return False

            if len(cmds) != no_cmd:
                self.fault_message = (
                    f"JOB COMMAND COUNT MISMATCH: length of {field_name} is {len(cmds)}, "
                    f"expected {no_cmd}"
                )
                self.console(f">> {self.fault_message}", level="error")
                return False

            expected_width = len(self.config.settings["main"]["work_order"])
            outport_num = self.config.settings["relay"]["outport_num"]

            padded_rows = []

            for row_index, row in enumerate(cmds, start=1):
                if not isinstance(row, list):
                    self.fault_message = (
                        f"JOB COMMAND ROW INVALID: {field_name}[{row_index}] is not a list"
                    )
                    self.console(f">> {self.fault_message}", level="error")
                    return False

                if len(row) != expected_width:
                    if allow_partial and 0 < len(row) < expected_width:
                        original_width = len(row)
                        row.extend([0] * (expected_width - len(row)))
                        padded_rows.append((row_index, original_width))
                    else:
                        self.fault_message = (
                            f"JOB COMMAND WIDTH MISMATCH: {field_name}[{row_index}] "
                            f"length is {len(row)}, expected {expected_width}"
                        )
                        self.console(f">> {self.fault_message}", level="error")
                        return False

                for col_index, relay_number in enumerate(row, start=1):
                    if type(relay_number) is not int:
                        self.fault_message = (
                            f"JOB COMMAND VALUE INVALID: {field_name}[{row_index}]"
                            f"[{col_index}]={relay_number!r} is not an integer"
                        )
                        self.console(f">> {self.fault_message}", level="error")
                        return False

                    if relay_number < 0 or relay_number > outport_num:
                        self.fault_message = (
                            f"JOB COMMAND VALUE OUT OF RANGE: {field_name}[{row_index}]"
                            f"[{col_index}]={relay_number}, expected 0..{outport_num}"
                        )
                        self.console(f">> {self.fault_message}", level="error")
                        return False

            if padded_rows:
                preview = ", ".join(
                    f"{row_index}:{original_width}->{expected_width}"
                    for row_index, original_width in padded_rows[:5]
                )
                if len(padded_rows) > 5:
                    preview += ", ..."
                self.console(
                    f">> JOB COMMAND WIDTH PADDED: {field_name} rows {preview}; "
                    "missing values set to 0",
                    level="warning",
                )

            self.logger.info(f"{field_name} check success")
            return True

        if job_type == "TESTADC":
            if "TestADCCmds" not in job or "NoTestADCCmds" not in job:
                self.fault_message = "JOB FILE MISSING TESTADC COMMAND KEYS"
                self.console(f">> {self.fault_message}", level="error")
                return None

            if job.get("DoADCTest", False) and cmds_check(
                job["TestADCCmds"],
                job["NoTestADCCmds"],
                "TestADCCmds",
                allow_partial=True,
            ):
                job["DataDir"] = job["DataDir"].replace("DataDC", "DataTestADC")
            else:
                self.fault_message = "TESTADC JOB DISABLED OR COMMAND CHECK FAILED"
                self.console(f">> {self.fault_message}", level="error")
                return None

        elif job_type == "TESTEL":
            if "TestCmds" not in job or "NoTestCmds" not in job:
                self.fault_message = "JOB FILE MISSING TESTEL COMMAND KEYS"
                self.console(f">> {self.fault_message}", level="error")
                return None

            if job.get("DoElectrodeTest", False) and cmds_check(
                job["TestCmds"],
                job["NoTestCmds"],
                "TestCmds",
                allow_partial=True,
            ):
                job["DataDir"] = job["DataDir"].replace("DataDC", "DataTestEl")
            else:
                self.fault_message = "TESTEL JOB DISABLED OR COMMAND CHECK FAILED"
                self.console(f">> {self.fault_message}", level="error")
                return None

        else:
            if "Cmds" not in job or "NoCmd" not in job:
                self.fault_message = "JOB FILE MISSING COMMAND KEYS"
                self.console(f">> {self.fault_message}", level="error")
                return None

            if not cmds_check(job["Cmds"], job["NoCmd"], "Cmds"):
                return None

        try:
            if not os.path.exists(job["DataDir"]):
                os.makedirs(job["DataDir"])
        except OSError as err:
            self.fault_message = f"DATA DIRECTORY CREATE FAIL: {job['DataDir']}"
            self.console(f">> {self.fault_message}: {repr(err)}", level="critical")
            return None

        filename_base = job["FileNameBase"].replace("/", "-")
        job["FileNameBase"] = filename_base

        savefilename = os.path.join(job["DataDir"], filename_base + ".json")

        try:
            with open(savefilename, "w", encoding="utf-8") as outfile:
                json.dump(job, outfile, indent=4)
        except OSError as err:
            self.fault_message = f"SAVE JOB COPY ERROR: {savefilename}"
            self.console(f">> {self.fault_message}: {repr(err)}", level="critical")
            return None

        self.console(">> load work file success")
        return job

    async def worker(self):
        while not self.stop_event.is_set():
            if self.job is None:
                await asyncio.sleep(0.1)
                continue

            self.console(">> worker() started")

            job = self.job
            self.job = None

            adc_settings = dict(self.config.settings["adc"])
            try:
                sample_rate = float(adc_settings.pop("sample_rate"))
                if sample_rate <= 0:
                    raise ValueError("sample_rate must be greater than 0")
            except (KeyError, TypeError, ValueError) as err:
                self.fault_message = "ADC SAMPLE RATE INVALID"
                self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                await self.safe_error_stop_async(job)
                continue

            try:
                busy_poll_interval_sec = float(
                    adc_settings.pop(
                        "busy_poll_interval_sec",
                        DEFAULT_BUSY_POLL_INTERVAL_SEC,
                    )
                )
                if busy_poll_interval_sec <= 0:
                    raise ValueError("busy_poll_interval_sec must be greater than 0")
            except (TypeError, ValueError) as err:
                self.fault_message = "ADC BUSY POLL INTERVAL INVALID"
                self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                await self.safe_error_stop_async(job)
                continue

            adc_comport = adc_settings.pop("comport", None)
            if "port" not in adc_settings and adc_comport is not None:
                adc_settings["port"] = adc_comport

            adc_param = {"pattern": 0}

            if job.get("TaskType") == "IP":
                adc_param["pattern"] = 2
            elif job.get("TaskType") == "DC":
                adc_param["pattern"] = 1
            else:
                adc_param["pattern"] = 0

            spms = sample_rate / 1000
            adc_param["on_samples"] = int(job["OnTime"] * spms)
            adc_param["off_samples"] = int(job["OffTime"] * spms)
            adc_param["cycles"] = job["NoStack"]
            adc_param["sample_rate_hz"] = sample_rate

            expected_adc_samples = (
                4
                * adc_param["cycles"]
                * (adc_param["on_samples"] + adc_param["off_samples"])
            )
            expected_adc_duration_sec = expected_adc_samples / sample_rate
            adc_param["busy_timeout_sec"] = max(
                DEFAULT_BUSY_TIMEOUT_SEC,
                expected_adc_duration_sec + 5.0,
            )
            adc_param["busy_poll_interval_sec"] = busy_poll_interval_sec
            self.logger.info(
                f"ADC busy timeout: expected_samples={expected_adc_samples}, "
                f"expected_duration={expected_adc_duration_sec:.2f}s, "
                f"busy_timeout={adc_param['busy_timeout_sec']:.2f}s, "
                f"busy_poll_interval={busy_poll_interval_sec:.2f}s"
            )

            power_param = {
                "voltage": job["MaxVval"],
                "current": job["MaxIval"],
            }

            do_electrode_test = job.get("DoElectrodeTest", False)
            do_adc_test = job.get("DoADCTest", False)

            if do_electrode_test:
                worklist = job["TestCmds"]
                self.console(f">> TestCmds START {worklist}")
                power_param["voltage"] = 30

            elif do_adc_test:
                worklist = job["TestADCCmds"]
                self.console(f">> TestADCCmds START {worklist}")

                power_param["voltage"] = 30

            else:
                worklist = job["Cmds"]
                self.console(f">> Cmds START {worklist}")

            try:
                if self.gpio is None:
                    raise RuntimeError("GPIO controller is not initialized")
                self.gpio.enable_relay_power()
                self.relay_power_enabled = True
                await asyncio.sleep(1.0)
            except Exception as err:
                self.fault_message = "RELAY POWER ENABLE FAIL"
                self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                await self.safe_error_stop_async(job)
                continue

            try:
                if self.relay is None:
                    raise RuntimeError("relay controller is not initialized")
                relay_clear_ok = await asyncio.to_thread(self.relay.test_clear)
                if not relay_clear_ok:
                    self.fault_message = "RELAY INIT CLEAR FAIL"
                    self.console(f">> {self.fault_message}", level="error")
                    await self.safe_error_stop_async(job)
                    continue
            except Exception as err:
                self.fault_message = "RELAY INIT CLEAR EXCEPTION"
                self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                await self.safe_error_stop_async(job)
                continue

            if do_adc_test:
                try:
                    self.gpio.enable_test_mode()
                    await asyncio.sleep(1.0)
                except Exception as err:
                    self.fault_message = "TEST MODE ENABLE FAIL"
                    self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                    await self.safe_error_stop_async(job)
                    continue

            try:
                if self.gpio is not None:
                    self.gpio.enable_booster()
                    await asyncio.sleep(1)
            except Exception as err:
                self.fault_message = "BOOSTER ENABLE FAIL"
                self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                await self.safe_error_stop_async(job)
                continue

            try:
                power_setup_result = await asyncio.to_thread(
                    self.power.set_target,
                    **power_param,
                )
            except Exception as err:
                self.fault_message = "POWER SETUP EXCEPTION"
                self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                await self.safe_error_stop_async(job)
                continue

            if not power_setup_result:
                self.fault_message = "POWER SETUP FAIL"
                self.console(f">> {self.fault_message}", level="error")
                await self.safe_error_stop_async(job)
                continue

            self.console(">> JOB START ------------------------")

            for idx, work in enumerate(worklist, start=1):
                if self.process_stop_event.is_set():
                    self.process_stop_event.clear()
                    self.fault_message = "CANCEL BY USER"
                    self.console(f">> {self.fault_message}", level="warning")
                    await self.safe_error_stop_async(job)
                    break

                self.logger.debug(f"idx={idx}, work={work}")
                self.console(f">> Work number : idx={idx}, work={work}")

                relay_set_ok = await asyncio.to_thread(
                    self.set_relay,
                    self.config.settings["main"]["work_order"],
                    work,
                )
                if not relay_set_ok:
                    self.fault_message = "RELAY SETUP FAIL"
                    self.console(f">> {self.fault_message}", level="error")
                    await self.safe_error_stop_async(job)
                    break

                file_name_base = job["FileNameBase"]
                filepath = os.path.join(
                    job["DataDir"],
                    file_name_base + "-" + f"{idx:03}" + ".dat",
                )

                try:
                    def capture_adc():
                        with adc_controller(**adc_settings, logger=self.logger) as adc:
                            return adc.capture(
                                output_file=filepath,
                                **adc_param,
                            )

                    received_samples = await asyncio.to_thread(capture_adc)
                    self.console(
                        f">> ADC Completed: received_samples={received_samples}"
                    )
                except Exception as err:
                    self.fault_message = "ADC CAPTURE FAIL"
                    self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                    await self.safe_error_stop_async(job)
                    break
                finally:
                    try:
                        if self.relay is not None and self.relay_power_enabled:
                            relay_clear_ok = await asyncio.to_thread(self.relay.clear)
                            if not relay_clear_ok:
                                self.console(
                                    ">> Relay clear after ADC capture returned no response",
                                    level="warning",
                                )
                    except Exception as err:
                        self.console(
                            f">> Relay clear after ADC capture error: {repr(err)}",
                            level="warning",
                        )

                try:
                    power_values = await asyncio.to_thread(self.power.monitoring_values)
                except Exception as err:
                    self.fault_message = "POWER MONITORING FAIL"
                    self.console(f">> {self.fault_message}: {repr(err)}", level="error")
                    await self.safe_error_stop_async(job)
                    break

                self.console(f">> Monitoring Power: {power_values}")

                if power_values.get("error_status", 0) != 0:
                    self.fault_message = (
                        f"POWER ERROR FAIL: error_status={power_values.get('error_status')}"
                    )
                    self.console(f">> {self.fault_message}", level="error")
                    await self.safe_error_stop_async(job)
                    break

                await asyncio.sleep(0.1)

            else:
                await self.safe_normal_stop_async(job)
                self.send_DONE()
                self.console(">> JOB done ------------------------")

    async def safe_error_stop_async(self, job):
        """
        Stop blocking hardware operations without blocking the asyncio event loop.
        State changes and UDP messages stay on the event-loop thread.
        """
        await self.safe_hardware_stop_async(job, "error")

        if self.fault_message == "CANCEL BY USER":
            self.job = None
            self.send_msg(self.fault_message)
            self.set_state("READY")
            self.console(
                f">> Cancel Stop and Send Message: {self.fault_message}",
                level="warning",
            )
            return

        if self.fault_message in ("ADC CAPTURE FAIL", "RELAY SETUP FAIL"):
            self.job = None
            self.send_msg("FAIL")
            self.send_msg(self.fault_message)
            self.set_state("READY")
            self.console(
                f">> Recoverable Error Stop and Send Fail: {self.fault_message}",
                level="error",
            )
            return

        self.send_FAIL()

        if self.fault_message:
            self.send_msg(self.fault_message)

        self.console(
            f">> Error Stop and Send Fail: {self.fault_message}",
            level="error",
        )

    async def safe_normal_stop_async(self, job):
        """
        Stop blocking hardware operations after a successful job without blocking UDP handling.
        """
        await self.safe_hardware_stop_async(job, "normal")

    async def safe_hardware_stop_async(self, job, stop_type):
        """
        Stop shared hardware resources without blocking the asyncio event loop.
        """
        try:
            if self.relay is not None and self.relay_power_enabled:
                relay_clear_ok = await asyncio.to_thread(self.relay.clear)
                if not relay_clear_ok:
                    self.console(
                        f">> Relay clear failed during {stop_type} stop: no response",
                        level="warning",
                    )
        except Exception as err:
            self.console(
                f">> Relay clear failed during {stop_type} stop: {repr(err)}",
                level="warning",
            )

        try:
            if self.power is not None:
                await asyncio.to_thread(self.power.stop)
        except Exception as err:
            self.console(
                f">> Power stop failed during {stop_type} stop: {repr(err)}",
                level="error",
            )

        try:
            if self.gpio is not None:
                self.gpio.disable_booster()
        except Exception as err:
            self.console(
                f">> Disable booster failed during {stop_type} stop: {repr(err)}",
                level="error",
            )

        if job.get("DoADCTest", False):
            try:
                if self.gpio is not None:
                    self.gpio.disable_test_mode()
                    await asyncio.sleep(1.0)
            except Exception as err:
                self.console(f">> Disable test mode failed: {repr(err)}", level="error")

        try:
            if self.gpio is not None and self.relay_power_enabled:
                self.gpio.disable_relay_power()
                self.relay_power_enabled = False
        except Exception as err:
            self.console(
                f">> Disable relay power failed during {stop_type} stop: {repr(err)}",
                level="error",
            )

    def set_relay(self, keys, values):
        result_dict = dict(zip(keys, values))
        self.logger.debug(result_dict)

        relay_set_value = [0] * self.config.settings["relay"]["outport_num"]

        for key in result_dict:
            if result_dict[key] != 0:
                try:
                    relay_index = result_dict[key] - 1
                    port_bit = self.config.settings["relay"]["port_order"][key] - 1

                    if relay_index < 0 or relay_index >= len(relay_set_value):
                        self.console(
                            f">> Relay index out of range: "
                            f"key={key}, relay_index={relay_index}",
                            level="error",
                        )
                        return False

                    if port_bit < 0:
                        self.console(
                            f">> Relay port bit invalid: key={key}, port_bit={port_bit}",
                            level="error",
                        )
                        return False

                    relay_set_value[relay_index] |= 0x1 << port_bit

                except KeyError:
                    self.console(f">> {key} is unknown port name", level="error")
                    return False
                except TypeError as err:
                    self.console(
                        f">> Invalid relay value: key={key}, err={repr(err)}",
                        level="error",
                    )
                    return False

        return self.relay.set(relay_set_value)

    async def run(self):
        await asyncio.gather(
            self.host_communicator(),
            self.worker(),
        )


if __name__ == "__main__":
    ers = None

    try:
        ers = ERSMainApp("/home/pi/ers_v2")

        if ers.device_state != "ERROR":
            asyncio.run(ers.run())
        else:
            print(">> ERS Main did not start because device_state is ERROR", flush=True)

    except KeyboardInterrupt:
        print(">> KeyboardInterrupt", flush=True)

    except Exception as err:
        if ers is not None and ers.logger is not None:
            ers.logger.exception(f"Unhandled exception: {repr(err)}")
        else:
            print(f"Unhandled exception: {repr(err)}", flush=True)

    finally:
        if ers is not None:
            ers.close()
