# --- ipv4 first: keep byte-identical in every script that goes online (tests/test_repo.py checks) ---
# Some networks hand out IPv6 addresses for NSE and Yahoo but drop IPv6 traffic. urllib then waits out the
# full timeout on each IPv6 address before it tries IPv4 (a live run took 40 to 80 s a request). Trying the
# IPv4 addresses first fixes that; IPv6 stays in the list, so an IPv6-only network still connects.
import socket as _socket

if not getattr(_socket.getaddrinfo, "ipv4_first", False):
    _plain_getaddrinfo = _socket.getaddrinfo

    def _getaddrinfo_ipv4_first(*args, **kwargs):
        return sorted(_plain_getaddrinfo(*args, **kwargs), key=lambda info: info[0] != _socket.AF_INET)

    _getaddrinfo_ipv4_first.ipv4_first = True
    _socket.getaddrinfo = _getaddrinfo_ipv4_first
# --- end ipv4 first ---
