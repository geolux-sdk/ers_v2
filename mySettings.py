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
            "busy_poll_interval_sec": 1.0,
            "save_csv": True,
            "csv_folder": "./log",
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
            "comport": "/dev/ttyAMA2",
            "baudrate": 115200,
            "timeout": 0.5,
            "device_id": 0xF1,
            "init_values": {
                "control_start_stop": 0,
                "target_voltage": 30,
                "target_load_current": 100,
                "input_voltage_gain": 150,
                "input_voltage_offset": 0,
                "load_voltage_gain": 150,
                "load_voltage_offset": 0,
                "load_current_gain": 1300,
                "load_current_offset": 20,
                "overvoltage_threshold": 400,
                "overcurrent_threshold": 2000,
                "soft_start_rate": 5000,
                "kp_cv": 0.01,
                "ki_cv": 0.002,
                "kd_cv": 0,
                "kp_cc": 0.5,
                "ki_cc": 0.01,
                "kd_cc": 0,
            },
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
                    "busy_poll_interval_sec": {"type": "number"},
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
                    "baudrate": {"type": "integer"},
                    "timeout": {"type": "number"},
                    "device_id": {"type": "integer"},
                    "init_values": {
                        "type": "object",
                        "properties": {
                            "control_start_stop": {"type": "integer"},
                            "target_voltage": {"type": "integer"},
                            "target_load_current": {"type": "integer"},
                            "input_voltage_gain": {"type": "integer"},
                            "input_voltage_offset": {"type": "integer"},
                            "load_voltage_gain": {"type": "integer"},
                            "load_voltage_offset": {"type": "integer"},
                            "load_current_gain": {"type": "integer"},
                            "load_current_offset": {"type": "integer"},
                            "overvoltage_threshold": {"type": "integer"},
                            "overcurrent_threshold": {"type": "integer"},
                            "soft_start_rate": {"type": "integer"},
                            "kp_cv": {"type": "number"},
                            "ki_cv": {"type": "number"},
                            "kd_cv": {"type": "number"},
                            "kp_cc": {"type": "number"},
                            "ki_cc": {"type": "number"},
                            "kd_cc": {"type": "number"},
                        },
                        "required": [
                            "control_start_stop",
                            "target_voltage",
                            "target_load_current",
                            "input_voltage_gain",
                            "input_voltage_offset",
                            "load_voltage_gain",
                            "load_voltage_offset",
                            "load_current_gain",
                            "load_current_offset",
                            "overvoltage_threshold",
                            "overcurrent_threshold",
                            "soft_start_rate",
                            "kp_cv",
                            "ki_cv",
                            "kd_cv",
                            "kp_cc",
                            "ki_cc",
                            "kd_cc",
                        ],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "comport",
                    "baudrate",
                    "timeout",
                    "device_id",
                    "init_values",
                ],
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

    def _logger_init(self):
        logger_folder = self.settings.get("logger", {}).get("folder", "./log")
        logger_filename = self.settings.get("logger", {}).get("filename", "app.log")
        logger_level = self.settings.get("logger", {}).get("level", "DEBUG")
        logger_consol = self.settings.get("logger", {}).get(
            "consol",
            self.settings.get("logger", {}).get("console", False),
        )

        os.makedirs(logger_folder, exist_ok=True)

        logger_path = os.path.join(logger_folder, logger_filename)

        log = logging.getLogger("ERS")
        log.handlers.clear()
        log.propagate = False

        level = getattr(logging, str(logger_level).upper(), logging.DEBUG)
        log.setLevel(level)

        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        file_handler = RotatingFileHandler(
            logger_path,
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
