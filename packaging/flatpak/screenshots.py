"""Render the screenshots the Flatpak metainfo points at, from a synthetic recording.

    QT_QPA_PLATFORM=offscreen python packaging/flatpak/screenshots.py

Writes ``packaging/flatpak/screenshots/viewer.png`` and ``export.png``. The recording is
written by ``adichtion.synth`` (made-up signals, no real data), so the images can be
published. Needs the ``[ui]`` extra. Run it again after a visible change to the viewer and
commit the images; the metainfo refers to them on the ``main`` branch.
"""
import os
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PYQTGRAPH_QT_LIB", "PySide6")
os.environ.setdefault("ADICHTION_THEME", "light")
os.environ["ADICHTION_SETTINGS_APP"] = "adichtion-screenshots"   # never the user's own settings

HERE = Path(__file__).resolve().parent
OUT = HERE / "screenshots"
SIZE = (1600, 900)                       # 16:9, what software centres show best


def recording(path: Path) -> None:
    """Two blocks of a made-up breathing test: flow, volume, a pressure and a slow trend."""
    from adichtion.synth import SynthChannel, SynthRecord, write_synthetic

    rate = 1000
    rng = np.random.default_rng(7)
    channels = [
        SynthChannel("Flow", units=("L/s", 0.5, 0.0)),
        SynthChannel("Volume", units=("L", 0.2, 0.0)),
        SynthChannel("Pressure", units=("cmH2O", 10.0, 0.0)),
        SynthChannel("Heart rate", units=("bpm", 10.0, 0.0)),
    ]

    def block(seconds: float, breath_s: float, depth: float, start: float):
        t = np.arange(int(seconds * rate)) / rate
        phase = 2 * np.pi * t / breath_s
        flow = depth * np.sin(phase) + 0.02 * rng.standard_normal(t.size)
        volume = np.cumsum(flow) / rate
        volume -= volume.min()
        pressure = 4 * np.sin(phase - 0.6) + 0.3 * rng.standard_normal(t.size)
        heart = 70 + 6 * np.sin(2 * np.pi * t / 40) + 0.5 * rng.standard_normal(t.size)
        data = {}
        for i, (c, values) in enumerate(zip(channels, (flow, volume, pressure, heart))):
            volts = (values - c.units[2]) / c.units[1]
            data[i] = np.clip(np.round(volts / c.stored_lsb), -32768, 32767).astype(np.int16)
        comments = [(int(seconds * rate * 0.25), -1, "Baseline"), (int(seconds * rate * 0.6), -1, "Exercise")]
        return SynthRecord(ticks=t.size, start_utc=start, comments=comments, data=data)

    records = [block(120, 4.0, 0.6, 1_700_000_000.0), block(90, 2.5, 1.2, 1_700_000_300.0)]
    write_synthetic(str(path), channels, records, tick_rate=rate)


def pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtGui import QPainter
    from PySide6.QtWidgets import QApplication

    with tempfile.TemporaryDirectory() as tmp:
        os.environ["HOME"] = os.environ["XDG_CONFIG_HOME"] = tmp
        for fmt in (QSettings.NativeFormat, QSettings.IniFormat):
            QSettings.setPath(fmt, QSettings.UserScope, tmp)
        rec = Path(tmp) / "breathing test.adicht"
        recording(rec)

        from adichtion.ui import theme
        from adichtion.ui.main_window import MainWindow

        app = QApplication.instance() or QApplication([])
        theme.apply_theme(app)
        w = MainWindow()
        w.resize(*SIZE)
        w.show()
        if not w.open_file_sync(str(rec)):
            print("could not open the synthetic recording", file=sys.stderr)
            return 1
        pump(app, 2.0)
        OUT.mkdir(exist_ok=True)
        w.grab().save(str(OUT / "viewer.png"))

        def grab_dialog() -> None:
            dlg = app.activeModalWidget()
            if dlg is not None:
                pump(app, 0.5)
                # offscreen windows are not composited: paint the dialog onto the window by hand
                shot = w.grab()
                inner = dlg.grab()
                painter = QPainter(shot)
                painter.drawPixmap((shot.width() - inner.width()) // 2,
                                   (shot.height() - inner.height()) // 2, inner)
                painter.end()
                shot.save(str(OUT / "export.png"))
                dlg.reject()

        QTimer.singleShot(500, grab_dialog)
        w.export_dialog("file")
        w.close()
        pump(app, 0.3)
    for name in ("viewer.png", "export.png"):
        if not (OUT / name).exists():
            print(f"{name} was not written", file=sys.stderr)
            return 1
        print(OUT / name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
