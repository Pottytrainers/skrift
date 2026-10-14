"""
Started by launchd the moment someone opens Skrift's link. launchd owns the
listening port while Skrift is asleep; this picks up those sockets and runs the
server on them. The server exits by itself after IDLE_EXIT_MINUTES with no
visits and no transcriptions running, and launchd starts it again on the next visit.
"""
import ctypes
import os
import socket
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

libc = ctypes.CDLL(None)
fds = ctypes.POINTER(ctypes.c_int)()
count = ctypes.c_size_t(0)
err = libc.launch_activate_socket(b"Web", ctypes.byref(fds), ctypes.byref(count))
if err != 0:
    sys.exit(f"launch_activate_socket failed: {os.strerror(err)}")
socks = [socket.socket(fileno=fds[i]) for i in range(count.value)]
libc.free(fds)
for s in socks:
    s.setblocking(False)

from skrift.server import serve  # noqa: E402

serve(socks)
