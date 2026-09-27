"""Reading channel settings from a (synthetic) LabChart 7 for Mac settings file."""
import struct

import pytest

from adichtion import macchart
from adichtion.macchart import (
    KEY_ARITH_EXPRESSION, KEY_ARITH_UNITS, KEY_CALCULATION, KEY_N_CHANNELS, KEY_RMS_SOURCE,
    KEY_SPIRO_FLOW_CAL, KEY_SPIRO_FLOW_SOURCE, KEY_SPIRO_VOL_RATIO, KEY_SPIRO_VOL_SOURCE, KEY_TITLE,
    MacChartError, MacResFile, channel_settings, document_properties, iter_properties,
)


def prop(typ: int, key: int, channel: int, payload: bytes) -> bytes:
    return b"\x00" + bytes([typ]) + struct.pack(">HI", key, channel) + payload


def pstr(text: str, trailer: bytes = b"") -> bytes:
    raw = text.encode("mac_roman") + trailer
    return bytes([len(raw)]) + raw


def f64(v: float) -> bytes:
    return struct.pack(">d", v)


def i32(v: int) -> bytes:
    return struct.pack(">i", v)


def settings_blob() -> bytes:
    return b"".join([
        b"junk\x01\x02",                                                   # anything unknown is skipped
        prop(0x04, KEY_N_CHANNELS, 0, i32(12)),                            # document level
        prop(0x08, KEY_TITLE, 6, pstr("Pdi")),
        prop(0x08, KEY_CALCULATION, 6, pstr("Arithmetic…")),          # menu label with the Mac Roman ellipsis
        prop(0x08, KEY_ARITH_EXPRESSION, 6, pstr("Ch9-Ch8", b"\x01")),     # expressions end with a stray 0x01
        prop(0x08, KEY_ARITH_UNITS, 6, pstr("cmH2O")),
        prop(0x08, KEY_TITLE, 11, pstr("Spiro Flow")),
        prop(0x08, KEY_CALCULATION, 11, pstr("Spiro. Flow…")),
        prop(0x04, KEY_SPIRO_FLOW_SOURCE, 11, i32(10)),
        prop(0x18, KEY_SPIRO_FLOW_CAL, 11, f64(13.29328270346598)),
        prop(0x08, KEY_TITLE, 12, pstr("Spiro Vol")),
        prop(0x08, KEY_CALCULATION, 12, pstr("Spiro. Volume…")),
        prop(0x04, KEY_SPIRO_VOL_SOURCE, 12, i32(11)),
        prop(0x18, KEY_SPIRO_VOL_RATIO, 12, f64(1.09)),
        prop(0x08, KEY_TITLE, 13, pstr("RMS EMGdi1")),
        prop(0x08, KEY_CALCULATION, 13, pstr("RMS…")),
        prop(0x0A, KEY_RMS_SOURCE, 13, struct.pack(">H", 1)),
        prop(0x04, KEY_SPIRO_FLOW_SOURCE, 13, i32(6)),                     # leftover of another extension's dialog
        prop(0x0C, 0x1234, 13, b"\x00\x01\x02\x03"),                       # 4 opaque bytes
        prop(0x08, KEY_TITLE, 7, pstr("Pmo")),                             # raw inputs simply have no calculation
    ])


def write_res_file(path, chunks: list[tuple[str, int, bytes]]) -> None:
    body = bytearray(macchart.MAGIC + b"\x00\x00\x00" + b"\x00" * 8)
    toc = bytearray()
    for typ, cid, data in chunks:
        toc += typ.encode("latin-1") + struct.pack(">HII", cid, len(body), len(data))
        body += data
    struct.pack_into(">II", body, 24, len(body), len(toc))
    path.write_bytes(bytes(body) + bytes(toc))


def test_iter_properties_resynchronises_and_types():
    props = list(iter_properties(settings_blob(), base_offset=1000))
    keys = [(p.channel, p.key) for p in props]
    assert (0, KEY_N_CHANNELS) in keys and (6, KEY_ARITH_EXPRESSION) in keys and (13, KEY_RMS_SOURCE) in keys
    by_key = {(p.channel, p.key): p for p in props}
    assert by_key[(6, KEY_ARITH_EXPRESSION)].value == "Ch9-Ch8"           # trailing 0x01 stripped
    assert by_key[(6, KEY_CALCULATION)].value == "Arithmetic…"
    assert by_key[(11, KEY_SPIRO_FLOW_CAL)].value == pytest.approx(13.29328270346598)
    assert by_key[(13, KEY_RMS_SOURCE)].value == 1
    assert by_key[(13, 0x1234)].value == b"\x00\x01\x02\x03"
    assert props[0].offset == 1000 + 6                                    # absolute offsets, junk skipped


def test_channel_settings_from_file(tmp_path):
    path = tmp_path / "settings"
    titles = [("STR ", 1100 + i, bytes([len(t)]) + t.encode("mac_roman")) for i, t in enumerate(["EMGdi1", "EMGdi2"])]
    write_res_file(path, [("Mcro", 30001, settings_blob()), ("vers", 1, b"\x07\x00"), *titles])
    f = MacResFile(path)
    assert not f.is_document
    assert [c.type for c in f.settings_chunks()] == ["Mcro"]
    assert f.channel_titles() == ["EMGdi1", "EMGdi2"]
    assert document_properties(f) == {KEY_N_CHANNELS: 12}

    s = channel_settings(path)
    assert s[1].title == "EMGdi1" and not s[1].is_calculated and s[1].describe() == "raw input"
    assert s[6].describe() == "Arithmetic: Ch9-Ch8 [cmH2O]"
    assert s[6].expression == "Ch9-Ch8" and s[6].calculation == "Arithmetic"
    assert s[11].describe() == "Spirometry flow of Ch10, 13.2933 L/s per V"
    assert s[12].describe() == "Spirometry volume of Ch11, volume ratio k = 1.09"
    assert s[12].volume_ratio == 1.09 and s[12].source == 11
    assert s[13].describe() == "Root mean square of Ch1" and s[13].source == 1
    assert s[13].flow_calibration is None                                # the leftover spirometry key is ignored for an RMS channel
    assert s[7].describe() == "raw input"
    assert list(s) == sorted(s)


def test_not_a_mac_file(tmp_path):
    path = tmp_path / "other"
    path.write_bytes(b"\x14ADInst Data Res Filx" + b"\x00" * 40)
    with pytest.raises(MacChartError):
        MacResFile(path)
    path.write_bytes(macchart.MAGIC + b"\x00" * 3 + struct.pack(">II", 99, 14) + b"\x00" * 20)
    with pytest.raises(MacChartError):
        MacResFile(path)
