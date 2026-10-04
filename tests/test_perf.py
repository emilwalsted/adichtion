"""The built-in timing: ``adichtion.perf`` (ADICHTION_PERF) and ``adichtion bench``.

Two promises are pinned here: nothing is timed or printed unless asked for, and only
numbers ever leave the process (no file names, titles, comment texts or dates)."""
import io
import json
import os

import numpy as np
import pytest

from adichtion import AdichtFile, perf
from adichtion.cli import main as cli_main
from adichtion.synth import SynthMacChannel, SynthMacRecord, write_mac_document


@pytest.fixture(autouse=True)
def _perf_off():
    perf.configure(False)
    yield
    perf.configure(False)


# -- the timeline -------------------------------------------------------------------------------

def test_off_by_default_nothing_is_timed_or_printed(capsys):
    assert not perf.enabled()
    a, b = perf.span("x", n=1), perf.span("y")
    assert a is b                                    # one shared do-nothing object: nothing allocated per call
    with a as sp:
        sp.set(blocks=3)
    perf.event("z", 1.0, n=2)
    perf.print_summary((1, 2))
    assert perf.summary() == {}
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err == ""


def test_environment_switch():
    assert perf.configure_from_env({"ADICHTION_PERF": "1"}) is True and perf.enabled()
    assert perf.configure_from_env({"ADICHTION_PERF": "0"}) is False and not perf.enabled()
    assert perf.configure_from_env({}) is False


def test_enabled_writes_lines_json_and_aggregates(tmp_path):
    out, path = io.StringIO(), tmp_path / "perf.jsonl"
    perf.configure(True, str(path), stream=out)
    with perf.span("render.detail", channel=2, buckets=1200) as sp:
        sp.set(decoded_blocks=3, level="envelope", flag=True)
    perf.event("gui.gap", 12.0, quiet=True)
    perf.event("gui.gap", 80.0)
    text = out.getvalue()
    assert "render.detail" in text and "decoded_blocks=3" in text and "level=envelope" in text and "flag=1" in text
    assert text.count("gui.gap") == 1                # the quiet one is aggregated only
    s = perf.summary()
    assert s["gui.gap"]["count"] == 2 and s["gui.gap"]["max_ms"] == 80.0 and s["gui.gap"]["total_ms"] == 92.0
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert [r["event"] for r in rows] == ["render.detail", "gui.gap"]
    assert rows[0]["decoded_blocks"] == 3 and rows[0]["ms"] >= 0
    perf.print_summary((90, 10))
    text = out.getvalue()
    assert "perf summary" in text and "hit_rate=0.9" in text and "decoded_blocks=10" in text


def test_only_numbers_can_be_logged():
    out = io.StringIO()
    perf.configure(True, stream=out)
    for bad in ({"title": "Flow"}, {"n": "3"}, {"level": "a comment text"}, {"n": None}, {"n": [1]}):
        with pytest.raises(ValueError):
            perf.event("e", 1.0, **bad)
    assert out.getvalue() == ""


# -- the file's cache counters ----------------------------------------------------------------

def test_cache_stats_count_decodes_and_hits(synthetic):
    with AdichtFile(synthetic["path"]) as f:
        assert f.cache_stats() == (0, 0)
        f.decode_block(0, 0, 0)
        assert f.cache_stats() == (0, 1)
        f.decode_block(0, 0, 0)
        f.decode_block(0, 0, 0)
        assert f.cache_stats() == (2, 1)
        f.clear_cache()
        assert f.cache_stats() == (0, 0)
        f.decode_block(0, 0, 0)
        assert f.cache_stats() == (0, 1)             # decoded again: the cache really was emptied


# -- adichtion bench ----------------------------------------------------------------------------

def _bench(args, capsys):
    cli_main(["bench", *args])
    return capsys.readouterr().out


def test_bench_json_has_every_measurement(synthetic, capsys):
    r = json.loads(_bench([synthetic["path"], "--json", "--export", "20"], capsys))
    assert r["file"]["records"] == 2 and r["file"]["channels"] == 4 and r["file"]["bytes"] == os.path.getsize(synthetic["path"])
    assert r["file"]["duration_s"] == pytest.approx(45.0)
    assert r["open_ms"] > 0 and r["global_range_ms"] >= 0
    views = {c["view"]: c for c in r["curves"]}
    assert set(views) == {"full", "5s"}              # the 10 min and 60 s views would be the whole 45 s file: not repeated
    assert views["5s"]["span_s"] == pytest.approx(5.0)
    for c in views.values():
        for k in ("coarse_cold_ms", "coarse_warm_ms", "detail_cold_ms", "detail_warm_ms", "detail_points"):
            assert c[k] >= 0
    assert views["5s"]["detail_decoded_blocks_cold"] > 0       # cold: the blocks really were decoded
    assert r["decode"]["blocks"] > 0 and r["decode"]["samples_per_block"] > 0
    assert r["cursor"]["lookups"] == 200
    assert r["export"]["rows"] == 20000 and r["export"]["channels"] == 4 and r["export"]["bytes"] > 0


def test_bench_table_and_output_file(synthetic, capsys, tmp_path):
    out_json = tmp_path / "bench.json"
    text = _bench([synthetic["path"], "--output", str(out_json)], capsys)
    for word in ("open", "y range", "curves", "decode", "cursor", "coarse cold", "detail cold"):
        assert word in text
    assert "export" not in text.split("cursor")[1]            # the export is opt-in
    assert json.loads(out_json.read_text())["export"] is None


def test_bench_reports_only_numbers(synthetic, tmp_path, capsys):
    """No file name, no channel titles, no comment texts, no dates (the privacy rule of the project)."""
    path = tmp_path / "SUBJ-0042 baseline.adicht"
    path.write_bytes(open(synthetic["path"], "rb").read())
    out = _bench([str(path), "--export"], capsys) + _bench([str(path), "--json"], capsys)
    low = out.lower()
    for forbidden in ("subj-0042", "baseline", "flow", "pmo", "pdi", "calc", "inh2o", "start", "stop", "short", "2016", "2015", str(tmp_path).lower()):
        assert forbidden not in low, forbidden
    assert not path.name in out


def test_bench_on_a_mac_document_with_a_calculated_channel(tmp_path, capsys):
    rng = np.random.default_rng(3)
    walk = lambda n, s: np.cumsum(rng.integers(-s, s + 1, n)).astype(np.int64)
    channels = [SynthMacChannel("EMGdi1", 1, "V", 2.048),
                SynthMacChannel("Pdi", 0, calculation="Arithmetic…", expression="Ch3-Ch2"),
                SynthMacChannel("Poes", 40, "V", 10.24, units=("cmH2O", 33.9, 1.4, True))]
    ticks = 20000
    rec = SynthMacRecord(ticks, [walk(ticks, 300), None, walk(-(-ticks // 40), 30)], 1_700_000_000.0, comments=[(4000, -1, "rest")])
    path = tmp_path / "Subject document"
    write_mac_document(path, channels, [rec], tick_rate=4000)
    r = json.loads(_bench([str(path), "--json", "--export"], capsys))
    assert r["file"]["kind"] == "mac" and r["file"]["channels"] == 3 and r["file"]["channels_with_data"] == 2
    assert r["curves"] and r["export"]["rows"] > 0


def test_bench_errors_are_plain(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        cli_main(["bench", str(tmp_path / "missing.adicht")])
    assert "not a file" in str(e.value)
    junk = tmp_path / "junk.adicht"
    junk.write_bytes(b"not a labchart file at all" * 10)
    with pytest.raises(SystemExit) as e:
        cli_main(["bench", str(junk)])
    assert str(e.value).startswith("error:")
