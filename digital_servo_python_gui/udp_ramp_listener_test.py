import socket
import pytest
from udp_ramp_listener import UDPRampListener


def make_listener_and_sender():
    """ Returns (listener, send_fn) where send_fn(msg: str) sends a datagram to the listener. """
    listener = UDPRampListener(port=0)  # OS picks a free port
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    def send(msg):
        sock.sendto(msg.encode(), ('127.0.0.1', listener.port))
    return listener, send


def test_empty_returns_empty_list():
    listener, _ = make_listener_and_sender()
    assert listener.drain() == []


def test_single_update():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch1 = 101.123\n')
    assert listener.drain() == [(1, 101.123)]


def test_negative_rate():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch2 = -50.0\n')
    assert listener.drain() == [(2, -50.0)]


def test_scientific_notation():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch3 = 1.5e3\n')
    assert listener.drain() == [(3, 1500.0)]


def test_zero_rate():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch1 = 0\n')
    assert listener.drain() == [(1, 0.0)]


def test_multiple_lines_in_one_datagram():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch1 = 10.0\nramp_rate_Hz_per_s_ch2 = 20.0\n')
    assert listener.drain() == [(1, 10.0), (2, 20.0)]


def test_multiple_datagrams_collected_in_one_drain():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch1 = 1.0\n')
    send('ramp_rate_Hz_per_s_ch2 = 2.0\n')
    assert listener.drain() == [(1, 1.0), (2, 2.0)]


def test_malformed_lines_ignored():
    listener, send = make_listener_and_sender()
    send('not_a_valid_key = 5.0\nramp_rate_Hz_per_s_ch4 = 99.9\njunk\n')
    assert listener.drain() == [(4, 99.9)]


def test_extra_whitespace_around_equals():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch1  =  42.0\n')
    assert listener.drain() == [(1, 42.0)]


def test_drain_clears_buffer():
    listener, send = make_listener_and_sender()
    send('ramp_rate_Hz_per_s_ch1 = 1.0\n')
    listener.drain()
    assert listener.drain() == []
