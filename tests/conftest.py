import os

import numpy as np
import pytest

from adichtion.synth import SynthChannel, SynthRecord, write_synthetic


@pytest.fixture(scope="session")
def synthetic(tmp_path_factory):
    """A small synthetic .adicht file with two records, three channels
    (two int16, one float32 with an unpacked block) and comments."""
    rng = np.random.default_rng(7)
    channels = [
        SynthChannel("Flow", "int16", None, 0.2),
        SynthChannel("Pmo", "int16", ("cmH2O", 13.34, -0.635), 10.0),
        SynthChannel("Calc", "float32", None, 10.0, unpacked_blocks=[(0, 1)]),
    ]
    records = []
    for ticks, comments in ((40000, [(10, -1, "start"), (16000 + 5, 1, "15,5 inH2O"), (39995, 0, "stop")]),
                            (5000, [(2500, -1, "short")])):
        r = SynthRecord(ticks, comments=comments)
        r.data = {
            0: np.clip(np.cumsum(rng.integers(-5, 6, ticks)), -32768, 32767),
            1: np.clip(3000 + np.cumsum(rng.integers(-3, 4, ticks)), -32768, 32767),
            2: (np.cumsum(rng.normal(0, 1, ticks)) * 0.37 + 100).astype(np.float32),
        }
        records.append(r)
    path = tmp_path_factory.mktemp("synth") / "synthetic.adicht"
    write_synthetic(path, channels, records)
    return {"path": str(path), "channels": channels, "records": records}


def samples_dir():
    """Folder with real .adicht files (and LabChart exports) for the gated integration tests."""
    d = os.environ.get("ADICHTION_SAMPLES")
    return d if d and os.path.isdir(d) else None
