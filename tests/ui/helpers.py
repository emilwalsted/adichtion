"""Event-loop helpers for the headless viewer tests."""
import time


def pump(app, seconds: float = 0.1) -> None:
    """Run the event loop for a while (worker results arrive as queued signals)."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def wait_until(app, cond, timeout: float = 10.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    app.processEvents()
    return cond()


