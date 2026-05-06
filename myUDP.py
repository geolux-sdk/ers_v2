import socket


class myUDP:
    def __init__(
        self, send_addr="127.0.0.1", send_port=3800, recv_port=3700, logger=None
    ):
        self.device_ip_port = (send_addr, send_port)
        self.logger = logger
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(1)
            self.sock.bind(("", recv_port))
        except (socket.error, OSError) as e:
            if self.logger:
                self.logger.critical(f"Socket error during initialization: {repr(e)}")
            raise
        except Exception as e:
            if self.logger:
                self.logger.critical(
                    f"Unexpected error during initialization: {repr(e)}"
                )
            raise

    def recv(self):
        try:
            data, address = self.sock.recvfrom(2048)
        except (socket.timeout, TimeoutError):
            return None
        except ConnectionResetError as err:
            if self.logger:
                self.logger.critical(f"Connection reset error: {repr(err)}")
            raise
        except socket.error as err:
            if self.logger:
                self.logger.error(f"Socket error during recv: {repr(err)}")
            raise
        except Exception as err:
            if self.logger:
                self.logger.error(f"Unexpected error during recv: {repr(err)}")
            raise
        else:
            if self.logger:
                self.logger.debug(f"Received from {address}: {data}")
            return data

    def close(self):
        try:
            self.sock.close()
        except socket.error as err:
            if self.logger:
                self.logger.error(f"Socket error during close: {repr(err)}")
            raise
        except Exception as err:
            if self.logger:
                self.logger.error(f"Unexpected error during close: {repr(err)}")
            raise

    def send(self, data):
        if not isinstance(data, bytes):
            raise ValueError("Data must be in bytes format")

        if self.logger:
            self.logger.debug(f"UDP send {data=}")

        try:
            self.sock.sendto(data, self.device_ip_port)
        except socket.error as err:
            if self.logger:
                self.logger.error(f"Socket error during send: {repr(err)}")
            raise
        except Exception as err:
            if self.logger:
                self.logger.error(f"Unexpected error during send: {repr(err)}")
            raise
