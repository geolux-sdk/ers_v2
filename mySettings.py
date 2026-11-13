import json
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from jsonschema import ValidationError, validate


class mySettings:
    defaults = {
        "logger": {
            "folder": "./log",
            "filename": "ERS.log",
            "level": "DEBUG",
            "consol": False,
            # "auto": systemd로 실행되면 journald가 보관하므로 파일을 만들지 않고,
            # 직접 실행하면 파일에 남긴다. true/false로 고정할 수도 있다.
            "file": "auto",
        },
        "name": "ERS V2 CONTROLLER",
        "version": "20260505",
        "main": {
            "job_path": "./JSON",
            "work_order": [
                "V+",
                "V-",
                "P1",
                "P2",
                "P3",
                "P4",
                "P5",
                "P6",
                "P7",
                "P8",
                "P9",
                "P10",
                "P11",
            ],
        },
        "relay": {
            "inport_num": 13,
            "port_order": {
                "V+": 1,
                "V-": 2,
                "P1": 3,
                "P2": 4,
                "P3": 5,
                "P4": 6,
                "P5": 7,
                "P6": 8,
                "P7": 9,
                "P8": 10,
                "P9": 11,
                "P10": 12,
                "P11": 13,
            },
            "outport_num": 48,
            "comport": "/dev/ttyAMA3",
        },
        "adc": {
            "port": "/dev/ttyACM0",
            "baudrate": 115200,
            "sample_rate": 2400,
            "save_csv": True,
            "csv_folder": "./csv",
        },
        "udp": {
            # 로컬 테스트 기본값
            # 실제 운용 시에는 Host PC의 IP로 변경
            # 예: "192.168.0.2"
            "send_addr": "127.0.0.1",
            "send_port": 3800,
            "recv_port": 3700,
        },
        "power": {
            # 새 POWER 보드(ESR_DSP). docs/power-board-dsp-modbus.md 10.1절
            "comport": "/dev/ttyAMA2",
            "address": 1,
            "min_voltage_v": 1.0,
            "max_voltage_v": 300.0,
            "min_current_a": 0.1,
            "max_current_a": 1.0,       # 펌웨어 상한. HW 정격 확인 후 조정
            "oc_ratio": 1.5,            # OC = 전류 지령 x 배율
            "oc_min_a": 0.3,
            "oc_max_a": 1.5,            # 펌웨어 상한. HW 정격 확인 후 조정
            "voltage_tolerance_v": 1.0,
            "voltage_tolerance_pct": 2.0,
            "voltage_settle_timeout_sec": 2.0,
            "keepalive_interval_sec": 1.0,
            "keepalive_fail_limit": 3,
            "test_voltage_v": 30.0,     # TESTEL/TESTADC 전압
            "run_time_margin_ratio": 1.5,
        },
    }

    schema = {
        "type": "object",
        "properties": {
            "logger": {
                "type": "object",
                "properties": {
                    "folder": {"type": "string"},
                    "filename": {"type": "string"},
                    "level": {"type": "string"},
                    "consol": {"type": "boolean"},
                    "file": {"type": ["boolean", "string"]},
                },
                "required": ["folder", "filename"],
            },
            "name": {"type": "string"},
            "version": {"type": "string"},
            "main": {
                "type": "object",
                "properties": {
                    "job_path": {"type": "string"},
                    "work_order": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                },
                "required": ["job_path", "work_order"],
            },
            "relay": {
                "type": "object",
                "properties": {
                    "inport_num": {"type": "integer"},
                    "outport_num": {"type": "integer"},
                    "comport": {"type": "string"},
                    "port_order": {
                        "type": "object",
                        "additionalProperties": {"type": "integer"},
                    },
                },
                "required": [
                    "inport_num",
                    "outport_num",
                    "comport",
                    "port_order",
                ],
            },
            "adc": {
                "type": "object",
                "properties": {
                    "port": {"type": "string"},
                    "comport": {"type": "string"},
                    "baudrate": {"type": "integer"},
                    "sample_rate": {"type": "number"},
                    "save_csv": {"type": "boolean"},
                    "csv_folder": {"type": "string"},
                },
                "anyOf": [
                    {"required": ["port"]},
                    {"required": ["comport"]},
                ],
                "required": ["baudrate", "sample_rate"],
            },
            "udp": {
                "type": "object",
                "properties": {
                    "send_addr": {
                        "type": "string",
                        "minLength": 1,
                    },
                    "send_port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                    },
                    "recv_port": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 65535,
                    },
                },
                "required": ["send_addr", "send_port", "recv_port"],
            },
            "power": {
                "type": "object",
                "properties": {
                    "comport": {"type": "string"},
                    "address": {"type": "integer", "minimum": 1, "maximum": 247},
                    "min_voltage_v": {"type": "number", "exclusiveMinimum": 0},
                    "max_voltage_v": {"type": "number", "exclusiveMinimum": 0},
                    "min_current_a": {"type": "number", "exclusiveMinimum": 0},
                    "max_current_a": {"type": "number", "exclusiveMinimum": 0},
                    "oc_ratio": {"type": "number", "exclusiveMinimum": 0},
                    "oc_min_a": {"type": "number", "exclusiveMinimum": 0},
                    "oc_max_a": {"type": "number", "exclusiveMinimum": 0},
                    "voltage_tolerance_v": {"type": "number", "exclusiveMinimum": 0},
                    "voltage_tolerance_pct": {"type": "number", "exclusiveMinimum": 0},
                    "voltage_settle_timeout_sec": {"type": "number", "exclusiveMinimum": 0},
                    "keepalive_interval_sec": {"type": "number", "exclusiveMinimum": 0, "maximum": 5},
                    "keepalive_fail_limit": {"type": "integer", "minimum": 1},
                    "test_voltage_v": {"type": "number", "exclusiveMinimum": 0},
                    "run_time_margin_ratio": {"type": "number", "minimum": 1},
                },
                "required": ["comport", "address"],
                # power_controller(**settings) 로 넘기므로 모르는 키는 거부한다.
                "additionalProperties": False,
            },
        },
        "required": [
            "logger",
            "name",
            "version",
            "main",
            "relay",
            "adc",
            "udp",
            "power",
        ],
    }

    def __init__(
        self,
        defaults: dict = None,
        file_name="settings.json",
        folder_name="./",
    ) -> None:
        self.defaults = defaults if defaults is not None else self.defaults

        os.makedirs(folder_name, exist_ok=True)
        self.file_path = os.path.join(folder_name, file_name)

        self.settings = self.read()
        self.validate(self.schema)
        self.logger = self._logger_init()

    @staticmethod
    def _use_file_handler(setting):
        """
        파일 로그를 남길지 결정한다.

        "auto"이면 실행 방식으로 판단한다. systemd는 유닛의 stdout을
        journald에 연결할 때 JOURNAL_STREAM을 설정하므로, 그 경우
        journald가 이미 로그를 보관한다고 보고 파일을 만들지 않는다.
        터미널에서 직접 실행하면 남는 곳이 없으므로 파일에 기록한다.
        """
        if isinstance(setting, str) and setting.strip().lower() == "auto":
            return "JOURNAL_STREAM" not in os.environ

        return bool(setting)

    def _logger_init(self):
        logger_conf = self.settings.get("logger", {})

        logger_folder = logger_conf.get("folder", "./log")
        logger_filename = logger_conf.get("filename", "app.log")
        logger_level = logger_conf.get("level", "DEBUG")
        logger_consol = logger_conf.get(
            "consol",
            logger_conf.get("console", False),
        )
        logger_file = self._use_file_handler(logger_conf.get("file", "auto"))

        log = logging.getLogger("ERS")
        log.handlers.clear()
        log.propagate = False

        level = getattr(logging, str(logger_level).upper(), logging.DEBUG)
        log.setLevel(level)

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        if logger_file:
            os.makedirs(logger_folder, exist_ok=True)

            file_handler = RotatingFileHandler(
                os.path.join(logger_folder, logger_filename),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            log.addHandler(file_handler)

        if logger_consol:
            stream_handler = logging.StreamHandler(sys.stdout)
            stream_handler.setLevel(level)
            stream_handler.setFormatter(formatter)
            log.addHandler(stream_handler)

        if not log.handlers:
            # 파일·콘솔이 모두 꺼져 있으면 로그가 통째로 사라진다.
            fallback_handler = logging.StreamHandler(sys.stdout)
            fallback_handler.setLevel(level)
            fallback_handler.setFormatter(formatter)
            log.addHandler(fallback_handler)

        log.info("Logging started")

        return log

    def _logger(self, message):
        if hasattr(self, "logger"):
            self.logger.debug(message)
        else:
            print("mySettings Logger:", message)

    def read(self):
        try:
            with open(self.file_path, "r", encoding="utf-8") as file:
                data = json.load(file)

            return data

        except FileNotFoundError as err:
            self._logger(f"File not found, using defaults: {err}")
            self.write(self.defaults)
            print(">> ------------ USING DEFAULTS -------------")
            return self.defaults

        except json.JSONDecodeError as err:
            self._logger(f"JSON decode error: {err}")
            raise

    def write(self, data: dict) -> None:
        try:
            with open(self.file_path, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4)
        except IOError as err:
            self._logger(f"IOError during write: {err}")
            raise

    def get_logger(self):
        return self.logger

    def validate(self, schema):
        try:
            validate(instance=self.settings, schema=schema)
        except ValidationError as err:
            self._logger(f"Validation error: {err}")
            raise


if __name__ == "__main__":
    config = mySettings()

    log = config.get_logger()
    log.info("Configuration read and validated.")

    config.write(config.settings)
