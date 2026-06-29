import re
import socket


class UDPRampListener:
    """ Listens on a UDP port for plaintext ramp rate updates of the form:
        ramp_rate_Hz_per_s_ch1 = 101.123
    Multiple lines may appear in a single datagram. """
    def __init__(self, port=7654):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('', port))
        self.sock.setblocking(False)
        self.port = self.sock.getsockname()[1]  # actual port (useful when port=0 was passed)
        self._pattern = re.compile(
            r'ramp_rate_Hz_per_s_ch(\d+)\s*=\s*([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)')

    def drain(self):
        """ Read all pending datagrams without blocking.
        Returns a list of (channel_id, rate_Hz_per_s) tuples. """
        results = []
        while True:
            try:
                data, _ = self.sock.recvfrom(4096)
                for line in data.decode(errors='replace').splitlines():
                    m = self._pattern.match(line.strip())
                    if m:
                        results.append((int(m.group(1)), float(m.group(2))))
            except BlockingIOError:
                break
        return results
