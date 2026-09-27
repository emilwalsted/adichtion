#!/usr/bin/env python3
"""Verify decoded samples against every LabChart text export in a folder.

Usage: verify_exports.py FILE.adicht RECORD_NUMBER EXPORT_DIR [--channels 1,2,3]

Each export must cover a time range of the given record (LabChart's
"StartOfBlock" time format).  Prints one line per export and channel; exit
status 1 if any int16 channel differs by more than float rounding.
"""
import argparse
import glob
import os
import sys

import numpy as np

from adichtion import AdichtFile
from adichtion.export import read_labchart_text


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file"); ap.add_argument("record", type=int); ap.add_argument("export_dir")
    ap.add_argument("--channels", default=None, help="1-based channels matching the export columns")
    ap.add_argument("--pattern", default="*.txt", help="glob for the export files (default *.txt)")
    a = ap.parse_args()
    bad = 0
    with AdichtFile(a.file) as f:
        rec = f.records[a.record - 1]
        chans = [int(c) - 1 for c in a.channels.split(",")] if a.channels else list(range(f.n_channels))
        for path in sorted(glob.glob(os.path.join(a.export_dir, a.pattern))):
            _h, t, vals, _c = read_labchart_text(path)
            if vals.shape[1] < len(chans):
                print(f"{os.path.basename(path)}: {vals.shape[1]} columns, skipped"); continue
            rate = rec.sample_rate_hz(chans[0]); start = int(round(t[0] * rate)); stop = start + len(t)
            for col, ch in enumerate(chans):
                x = rec.read(ch, start, stop); m = min(len(x), len(vals)); x, y = x[:m], vals[:m, col]
                if m == 0:
                    print(f"{os.path.basename(path)}: no overlap with record {a.record}"); break
                A = np.vstack([x, np.ones_like(x)]).T; (k, b), *_ = np.linalg.lstsq(A, y, rcond=None)
                res = np.abs(y - (k * x + b)).max(); exact = np.abs(y - x).max()
                status = "exact" if exact < 1e-5 else ("scaled" if res < 1e-5 else "MISMATCH")
                if status == "MISMATCH" and f.channels[ch].dtype == "int16":
                    bad += 1
                print(f"{os.path.basename(path)}: ch{ch + 1} {f.channel_titles[ch]:14} {status:8} max|diff|={exact:.3g} fit k={k:.6g} b={b:.4g} resid={res:.3g}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
