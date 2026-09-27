import os

import numpy as np
import pytest

from adichtion.synth import SynthChannel, SynthRecord, write_synthetic

VARIANTS = {
    "v29": dict(version=29, layout="sequential", video=True),
    "v26": dict(version=26, layout="sequential", video=False),
    "legacy": dict(version=29, layout="legacy", video=False),
}


def make_document():
    """Two records, four channels (two int16, a float32 with an unpacked block and a
    float32 with a units conversion) and comments.  Same content for every variant."""
    rng = np.random.default_rng(7)
    channels = [
        SynthChannel("Flow", "int16", None, 0.2),
        SynthChannel("Pmo", "int16", ("cmH2O", 13.34, -0.635), 10.0),
        SynthChannel("Calc", "float32", None, 10.0, unpacked_blocks=[(0, 1)], lsb=0.2 * 1.024 / 32768),
        SynthChannel("Pdi", "float32", ("cmH2O", 1.024, 0.0), 10.0),
    ]
    records = []
    specs = ((40000, [(10, -1, "start"), (16000 + 5, 1, "15,5 inH2O"), (39995, 0, "stop")],
              ("Record1.wmv", 416.67, 455.65, 416.38, 0.46)),
             (5000, [(2500, -1, "short")], None))
    for ticks, comments, video in specs:
        r = SynthRecord(ticks, comments=comments, videos=video)
        r.data = {
            0: np.clip(np.cumsum(rng.integers(-5, 6, ticks)), -32768, 32767),
            1: np.clip(3000 + np.cumsum(rng.integers(-3, 4, ticks)), -32768, 32767),
            2: (np.cumsum(rng.normal(0, 1, ticks)) * 0.37 + 100).astype(np.float32),
            3: (np.cumsum(rng.normal(0, 30, ticks)) + 3e5).astype(np.float32),
        }
        records.append(r)
    return channels, records


@pytest.fixture(scope="session")
def documents(tmp_path_factory):
    """``{variant: {"path", "channels", "records"}}`` for every layout variant."""
    channels, records = make_document()
    out = {}
    folder = tmp_path_factory.mktemp("synth")
    for name, kw in VARIANTS.items():
        path = folder / f"synthetic_{name}.adicht"
        write_synthetic(path, channels, records, **kw)
        out[name] = {"path": str(path), "channels": channels, "records": records, **kw}
    return out


@pytest.fixture(scope="session")
def synthetic(documents):
    """The v29 document (the flavour current LabChart versions write)."""
    return documents["v29"]


@pytest.fixture(scope="session", params=list(VARIANTS))
def any_document(request, documents):
    return documents[request.param]


def samples_dir():
    """Folder with real .adicht files (and LabChart exports) for the gated integration tests."""
    d = os.environ.get("ADICHTION_SAMPLES")
    return d if d and os.path.isdir(d) else None
