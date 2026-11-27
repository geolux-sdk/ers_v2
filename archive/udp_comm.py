import socket
import logging


logging.getLogger(__name__).addHandler(logging.NullHandler())


class UDPComm:
    def __init__(
        self,
        send_addr="127.0.0.1",
        send_port=3800,
        recv_addr="0.0.0.0",
        recv_port=3700,
        timeout=1.0,
        logger=None,
    ):
        self.device_ip_port = (send_addr, send_port)
        self.recv_addr = recv_addr
        self.recv_port = recv_port
        self.timeout = timeout
        self.logger = logger or logging.getLogger(__name__)
        self.sock = None

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(self.timeout)
            self.sock.bind((self.recv_addr, self.recv_port))

            self.logger.info(
                "UDP socket opened: recv=%s:%s, send=%s:%s, timeout=%s",
                self.recv_addr,
                self.recv_port,
                send_addr,
                send_port,
                self.timeout,
            )

        except (socket.error, OSError) as err:
            self.logger.critical(
                "Socket error during initialization: %r",
                err,
            )
            self.close()
            raise

        except Exception as err:
            self.logger.critical(
                "Unexpected error during initialization: %r",
                err,
            )
            self.close()
            raise

    def recv(self):
        """
        Receive UDP data.

        Returns:
            bytes: received data
            None : timeout
        """
        if self.sock is None:
            raise RuntimeError("UDP socket is closed")

        try:
            data, address = self.sock.recvfrom(2048)

        except (socket.timeout, TimeoutError):
            return None

        except ConnectionResetError as err:
            self.logger.critical("Connection reset error: %r", err)
            raise

        except socket.error as err:
            self.logger.error("Socket error during recv: %r", err)
            raise

        except Exception as err:
            self.logger.error("Unexpected error during recv: %r", err)
            raise

        self.logger.debug("UDP received from %s: %r", address, data)
        return data

    def recv_from(self):
        """
        Receive UDP data with sender address.

        Returns:
            tuple: (data, address)
            None : timeout
        """
        if self.sock is None:
            raise RuntimeError("UDP socket is closed")

        try:
            data, address = self.sock.recvfrom(2048)

        except (socket.timeout, TimeoutError):
            return None

        except ConnectionResetError as err:
            self.logger.critical("Connection reset error: %r", err)
            raise

        except socket.error as err:
            self.logger.error("Socket error during recv_from: %r", err)
            raise

        except Exception as err:
            self.logger.error("Unexpected error during recv_from: %r", err)
            raise

        self.logger.debug("UDP received from %s: %r", address, data)
        return data, address

    def send(self, data):
        """
        Send UDP data to default destination.
        """
        if self.sock is None:
            raise RuntimeError("UDP socket is closed")

        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes or bytearray")

        self.logger.debug("UDP send: %r", data)

        try:
            self.sock.sendto(data, self.device_ip_port)

        except socket.error as err:
            self.logger.error("Socket error during send: %r", err)
            raise

        except Exception as err:
            self.logger.error("Unexpected error during send: %r", err)
            raise

    def send_to(self, data, addr, port):
        """
        Send UDP data to specified destination.
        """
        if self.sock is None:
            raise RuntimeError("UDP socket is closed")

        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("data must be bytes or bytearray")

        target = (addr, port)

        self.logger.debug("UDP send to %s: %r", target, data)

        try:
            self.sock.sendto(data, target)

        except socket.error as err:
            self.logger.error("Socket error during send_to: %r", err)
            raise

        except Exception as err:
            self.logger.error("Unexpected error during send_to: %r", err)
            raise

    def close(self):
        """
        Close UDP socket.
        """
        if self.sock is None:
            return

        try:
            self.sock.close()
            self.logger.info("UDP socket closed")

        except socket.error as err:
            self.logger.error("Socket error during close: %r", err)
            raise

        except Exception as err:
            self.logger.error("Unexpected error during close: %r", err)
            raise

        finally:
            self.sock = None

    def __enter__(self):
        """
        Support with-statement.
        """
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """
        Auto close when exiting with-statement.
        """
        self.close()