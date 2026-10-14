"""What the Skrift icon runs: start Skrift on this computer (if it isn't already running) and open it in the
default browser. It keeps running while the page is open or a transcript is working, and quits by itself
15 minutes after the last tab closes."""
import os
import socket
import threading
import urllib.request
import webbrowser


def _running(url: str) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/health", timeout=2) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def _port_free(port: int) -> bool:
    with socket.socket() as s:
        if os.name != "nt":  # like the server itself: a port the setup page just closed is free (TIME_WAIT)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def main():
    from .tools import setup_path
    setup_path()
    port = int(os.environ.get("SKRIFT_PORT", "5200"))
    url = f"http://localhost:{port}"
    if _running(url):  # already open in the background: just show it
        if not os.environ.get("SKRIFT_NO_BROWSER"):
            webbrowser.open(url)
        return
    while not _port_free(port):  # something else uses the port
        port += 1
        url = f"http://localhost:{port}"
    os.environ.setdefault("IDLE_EXIT_MINUTES", "15")
    if not os.environ.get("SKRIFT_NO_BROWSER"):  # the first-run setup page is already open and switches over
        threading.Timer(1.5, webbrowser.open, [url]).start()
    import importlib
    server = importlib.import_module("skrift.server")
    server.IDLE_MINUTES = float(os.environ["IDLE_EXIT_MINUTES"])
    server.serve(port=port)


if __name__ == "__main__":
    main()
