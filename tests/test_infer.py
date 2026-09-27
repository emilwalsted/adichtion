"""The numerical inference of calculated-channel definitions, on a synthetic document
whose float32 channels are built with the standard LabChart calculations."""
import numpy as np
import pytest

from adichtion import AdichtFile
from adichtion.infer import infer_channel, infer_record, spirometry_volume, spirometry_volume_parts
from adichtion.synth import SynthChannel, SynthRecord, write_synthetic

TICK_RATE = 1000
N = 60_000
FLOW_CAL = 13.29328       # L/s per V
FLOW_LSB = 10 * 1.024 / 32768
PRESSURE_LSB = 10 * 1.024 / 32768
F32 = 2.0 ** -15


def moving_rms(x: np.ndarray, before: int, after: int) -> np.ndarray:
    """Centred moving RMS with the window truncated at the edges (how the edges are done does not matter here)."""
    cs = np.concatenate([[0.0], np.cumsum(x.astype(np.float64) ** 2)])
    i = np.arange(len(x))
    lo = np.maximum(i - before, 0)
    hi = np.minimum(i + after + 1, len(x))
    return np.sqrt((cs[hi] - cs[lo]) / (hi - lo))


@pytest.fixture(scope="module")
def derived_document(tmp_path_factory):
    rng = np.random.default_rng(11)
    t = np.arange(N) / TICK_RATE
    dt = 1.0 / TICK_RATE
    # raw flow: 4 s breaths of about 0.9 L (amplitude 170 counts = 0.7 L/s), quiet for the first and
    # last half second, and a zero drifting from +5 to -40 counts
    flow_signal = 170 * np.sin(2 * np.pi * t / 4.0)
    flow_signal[:500] = 0
    flow_signal[-500:] = 0
    zero = 5 + (-40 - 5) * np.arange(N) / (N - 1)
    flow_counts = np.round(flow_signal + zero).astype(np.int64)
    emg_counts = np.round(rng.normal(0, 1, N) * (300 + 250 * np.sin(2 * np.pi * t / 4.0))).astype(np.int64)
    pmo_counts = np.clip(3000 + np.cumsum(rng.integers(-3, 4, N)), -32768, 32767)
    poes_counts = np.clip(-1500 + np.cumsum(rng.integers(-3, 4, N)), -32768, 32767)
    pmo_units, poes_units = ("cmH2O", 34.55, -0.86), ("cmH2O", 33.86, 1.385)
    pmo = pmo_units[1] * PRESSURE_LSB * pmo_counts + pmo_units[2]
    poes = poes_units[1] * PRESSURE_LSB * poes_counts + poes_units[2]
    # the calculated channels, as physical values
    pdi = pmo - poes
    spiro_flow = FLOW_CAL * FLOW_LSB * (flow_counts - zero)
    spiro_flow_f32 = (spiro_flow / (FLOW_CAL * FLOW_LSB)).astype(np.float32).astype(np.float64) * FLOW_CAL * FLOW_LSB
    spiro_vol = spirometry_volume(spiro_flow_f32, dt, 0.4, 1.09, -1)
    volume = np.cumsum(spiro_flow_f32) * dt
    ex_flow = (spiro_flow_f32 + np.abs(spiro_flow_f32)) / 2
    shifted = spiro_flow_f32 + 0.34
    in_flow = (shifted - np.abs(shifted)) / 2
    mix = 0.5 * pmo + 2 * poes + 1.5
    rms = moving_rms(emg_counts, 25, 24)
    channels = [
        SynthChannel("Flow (raw)", "int16", None, 10.0),
        SynthChannel("Pmo", "int16", pmo_units, 10.0),
        SynthChannel("Poes", "int16", poes_units, 10.0),
        SynthChannel("Pdi", "float32", ("cmH2O", 1.024, 0.0), lsb=F32),
        SynthChannel("Spiro Flow", "float32", ("L/s", FLOW_CAL, 0.0), lsb=FLOW_LSB),
        SynthChannel("Spiro Vol", "float32", ("L", 20.0, 0.0), lsb=FLOW_LSB),
        SynthChannel("EMG", "int16", None, 2.0),
        SynthChannel("RMS EMG", "float32", None, lsb=2 * 1.024 / 32768),
        SynthChannel("Vol", "float32", ("L", 1.024, 0.0), lsb=F32),
        SynthChannel("Ex Flow", "float32", ("L/s", 1.024, 0.0), lsb=F32),
        SynthChannel("Mix", "float32", ("mix", 1.024, 0.0), lsb=F32),
        SynthChannel("In Flow", "float32", ("L/s", 1.024, 0.0), lsb=F32),
    ]
    rec = SynthRecord(N)
    rec.data = {
        0: flow_counts, 1: pmo_counts, 2: poes_counts,
        3: (pdi / (1.024 * F32)).astype(np.float32),
        4: (spiro_flow / (FLOW_CAL * FLOW_LSB)).astype(np.float32),
        5: (spiro_vol / (20.0 * FLOW_LSB)).astype(np.float32),
        6: emg_counts,
        7: rms.astype(np.float32),
        8: (volume / (1.024 * F32)).astype(np.float32),
        9: (ex_flow / (1.024 * F32)).astype(np.float32),
        10: (mix / (1.024 * F32)).astype(np.float32),
        11: (in_flow / (1.024 * F32)).astype(np.float32),
    }
    path = tmp_path_factory.mktemp("derived") / "derived_v26.adicht"
    write_synthetic(path, channels, [rec], tick_rate=TICK_RATE, version=26)
    return str(path)


def test_spirometry_volume_by_hand():
    flow = np.array([0.0, -1, -1, -1, 1, 1, -1, -1])
    v = spirometry_volume(flow, 1.0, dead_space=1.5, ratio=2.0, corrected_sign=-1)
    # 0.5 L of dead space is left after sample 1, so sample 2 is divided as a whole;
    # the two positive samples refill the reservoir (capped at 1.5 L)
    assert np.allclose(v, [0, -1, -1.5, -2, -1, 0, -1, -1.5])
    plain, corrected = spirometry_volume_parts(flow, 1.0, 1.5, -1)
    assert np.allclose(plain, [0, -1, -1, -1, 0, 1, 0, 0]) and np.allclose(corrected, [0, 0, -1, -2, -2, -2, -2, -3])
    # a huge dead space is a plain integral (sample 0 included), and the other sign is symmetric
    assert np.allclose(spirometry_volume(flow, 1.0, 1e9, 2.0, -1), np.cumsum(flow))
    assert np.allclose(spirometry_volume(np.array([2.0, 1.0]), 1.0, 1e9, 2.0, -1), [2, 3])
    assert np.allclose(spirometry_volume(-flow, 1.0, 1.5, 2.0, +1), -v)
    # a record that starts in the corrected phase drains the full reservoir first
    assert np.allclose(spirometry_volume(np.array([-1.0, -1, -1]), 1.0, 1.5, 2.0, -1), [-1, -1.5, -2])


def test_infer_recovers_every_definition(derived_document):
    with AdichtFile(derived_document) as f:
        rec = f.records[0]
        results = infer_record(rec, length=N)
        best = {c: hyps[0] for c, hyps in results.items()}
        titles = f.channel_titles
        assert set(best) == {3, 4, 5, 7, 8, 9, 10, 11}, [titles[c] for c in results if not results[c]]

        pdi = best[3]
        assert pdi.kind == "arithmetic" and pdi.description == "Ch2-Ch3" and pdi.exact

        flow = best[4]
        assert flow.kind == "spiro_flow" and flow.sources == (1,)
        assert flow.params["a"] == pytest.approx(FLOW_CAL, rel=1e-4)
        assert (flow.params["zero_first"], flow.params["zero_last"]) == (5, -40)
        assert flow.exact or flow.within_lsb

        vol = best[5]
        assert vol.kind == "spiro_volume" and vol.sources == (5,) and vol.exact
        assert vol.params["dead_space"] == pytest.approx(0.4) and vol.params["ratio"] == pytest.approx(1.09)
        assert vol.params["corrected_sign"] == -1

        rms = best[7]
        assert rms.kind == "rms" and rms.sources == (7,) and rms.exact
        assert (rms.params["window"], rms.params["before"], rms.params["after"]) == (50, 25, 24)

        integral = best[8]
        assert integral.kind == "integral" and integral.sources == (5,) and integral.exact

        ex = best[9]
        assert ex.kind == "rectify" and ex.description == "(Ch5+Abs(Ch5))/2" and ex.exact

        mix = best[10]
        assert mix.kind == "arithmetic" and mix.description == "0.5*Ch2+2*Ch3+1.5" and mix.exact

        shifted = best[11]
        assert shifted.kind == "rectify" and shifted.description == "(Ch5+0.34-Abs(Ch5+0.34))/2" and shifted.exact


def test_infer_channel_options(derived_document):
    with AdichtFile(derived_document) as f:
        rec = f.records[0]
        # restricting the sources restricts the hypotheses
        only_pressures = infer_channel(rec, 3, sources=[1, 2], length=20_000)
        assert only_pressures and only_pressures[0].description == "Ch2-Ch3"
        assert all(set(h.sources) <= {2, 3} for h in only_pressures)
        # a raw channel is explained by nothing
        assert infer_channel(rec, 6, length=20_000) == []
        # too short a range gives nothing rather than nonsense
        assert infer_channel(rec, 3, start=0, length=50) == []
        # the plain integral is not offered for a range that does not start at the record start
        later = infer_channel(rec, 8, start=10_000, length=20_000)
        assert all(h.kind not in ("integral", "spiro_volume") for h in later)
