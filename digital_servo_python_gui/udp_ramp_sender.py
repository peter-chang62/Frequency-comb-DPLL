""" Simple UDP ramp rate sender.
Run directly to send one update per second to a local UDPRampListener (e.g. the GUI).
"""
import socket
import time


class UDPRampSender:
    def __init__(self, host='localhost', port=7654):
        self.host = host
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send_ramp_rate(self, channel_id, rate_Hz_per_s):
        msg = f'ramp_rate_Hz_per_s_ch{channel_id} = {rate_Hz_per_s}\n'
        self.sock.sendto(msg.encode(), (self.host, self.port))


if __name__ == '__main__':
    sender = UDPRampSender(host='localhost', port=7654)
    rates = [0.1, 0.5, 1.0, -0.5, 0.0]
    channel_id = 1
    step = 0
    print(f'Sending ramp rate updates to localhost:7654  (Ctrl-C to stop)')
    while True:
        rate = rates[step % len(rates)]
        sender.send_ramp_rate(channel_id, rate)
        print(f'  ramp_rate_Hz_per_s_ch{channel_id} = {rate}')
        step += 1
        time.sleep(1.0)
