"""Infer the definitions of calculated channels from the data itself.

``.adicht`` files written by LabChart 7 for Mac ("Read/Write WinChart Files")
store the *results* of channel calculations but not their definitions.  When
the other channels of the same record are the calculation's inputs, the
definition can usually be recovered by testing the standard LabChart
calculations against the data:

* **Arithmetic**: a linear combination of one or two other channels
  (``Ch9-Ch8``, ``1.095*Ch18+Ch19``), or of the positive and negative parts of
  one channel (``(Ch11+Abs(Ch11))/2``);
* **RMS**: a moving root-mean-square of a raw channel (window length and
  alignment found by search);
* **Integral**: the running integral of another channel, reset at the start of
  the record;
* **Spirometry Flow**: a raw flow signal scaled to L/s after a linear zero
  correction between the record's first and last sample;
* **Spirometry Volume**: the integral of a flow channel with LabChart's
  dead-space / volume-ratio correction (see :func:`spirometry_volume`);
* **scaled copy**: ``a * x + b`` of a raw channel.

Every hypothesis is scored by its maximum absolute error over the analysed
samples.  *Exact* means within the float32 quantisation of the stored channel;
*within one LSB* means the error does not exceed one count of the raw inputs,
which is what one gets when the calculation ran on a finer version of the
input than the whole counts stored in the file.  Channel numbers in the
descriptions are 1-based, as in LabChart.  ``docs/CALCULATED_CHANNELS.md``
has the background and the evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import numpy as np

from .file import AdichtFile, Record

__all__ = ["Hypothesis", "infer_channel", "infer_record", "spirometry_volume", "spirometry_volume_parts"]


@dataclass
class Hypothesis:
    kind: str                     # "arithmetic", "rectify", "rms", "integral", "copy", "spiro_flow", "spiro_volume"
    description: str              # LabChart-style, 1-based channel numbers
    max_abs_error: float          # in the units of the target channel
    tolerance: float              # error accepted as "exact" (float32 quantisation of the target)
    sources: tuple[int, ...]      # 1-based source channels
    params: dict = field(default_factory=dict)
    lsb_error: Optional[float] = None   # error expressed in LSBs of the raw inputs, when applicable
    lsb_limit: float = 1.05             # largest lsb_error still reported as "within the input resolution"
    note: str = ""

    @property
    def exact(self) -> bool:
        return self.max_abs_error <= self.tolerance

    @property
    def within_lsb(self) -> bool:
        return self.lsb_error is not None and self.lsb_error <= self.lsb_limit

    @property
    def quality(self) -> str:
        if self.exact:
            return "exact"
        if self.within_lsb:
            return f"within {self.lsb_error:.2f} LSB of the inputs"
        return f"max |error| {self.max_abs_error:.3g}"

    def __str__(self) -> str:
        return f"{self.description}  ({self.quality}{'; ' + self.note if self.note else ''})"


# ----------------------------------------------------------------------------- helpers

def _ulp32(x: float) -> float:
    x = abs(float(x))
    return float(np.spacing(np.float32(x))) if x > 0 else float(np.spacing(np.float32(1.0)))


def _phys_scale(rec: Record, channel: int) -> float:
    """Physical value of one stored unit of a channel (its LSB after the units conversion)."""
    u = rec.units(channel)
    if u is None or not u.has_lsb:
        return 1.0
    return abs(u.slope * u.lsb) if u.enabled else u.lsb


def _quantisation(rec: Record, channel: int, stored: np.ndarray) -> float:
    """Physical size of one float32 ulp of the stored values (their coarsest step)."""
    return _ulp32(np.abs(stored).max() if len(stored) else 1.0) * _phys_scale(rec, channel)


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    """Pearson correlation, 0 when either input is constant."""
    if len(a) < 2 or np.ptp(a) == 0 or np.ptp(b) == 0:
        return 0.0
    with np.errstate(invalid="ignore", divide="ignore"):
        r = float(np.corrcoef(a, b)[0, 1])
    return r if np.isfinite(r) else 0.0


def _fmt(v: float) -> str:
    r = round(v, 6)
    return f"{r:g}" if abs(r) >= 1e-4 or r == 0 else f"{v:.3e}"


def _lin_desc(terms: list[tuple[float, str]], const: float, tol: float) -> str:
    parts = []
    for a, name in sorted(terms, key=lambda t: (t[0] < 0, t[1])):      # positive terms first, as one would write it
        if a == 0:
            continue
        if abs(a - 1.0) < 1e-9:
            parts.append(f"+{name}")
        elif abs(a + 1.0) < 1e-9:
            parts.append(f"-{name}")
        else:
            parts.append(f"{'+' if a >= 0 else '-'}{_fmt(abs(a))}*{name}")
    s = "".join(parts).lstrip("+")
    if abs(const) > tol:
        s += f"{'+' if const >= 0 else '-'}{_fmt(abs(const))}"
    return s or "0"


def _units_label(rec: Record, channel: int) -> str:
    u = rec.units(channel)
    if u is None:
        return ""
    if u.enabled and u.units:
        return u.units
    return "V" if u.has_lsb else ""


class _Fitter:
    """Least squares on a subsample, exact error on the full range, with coefficient snapping."""

    def __init__(self, y: np.ndarray, step: int):
        self.y = y
        self.sub = slice(None, None, step)
        self.ones = np.ones_like(y)

    def design(self, cols: Sequence[np.ndarray]) -> np.ndarray:
        return np.vstack(list(cols) + [self.ones]).T

    def fit(self, cols: Sequence[np.ndarray]) -> tuple[np.ndarray, float]:
        A = self.design(cols)
        coef, *_ = np.linalg.lstsq(A[self.sub], self.y[self.sub], rcond=None)
        return coef, float(np.abs(A @ coef - self.y).max())

    def snap(self, cols: Sequence[np.ndarray], coef: np.ndarray, bound: float) -> tuple[np.ndarray, float]:
        """Round the coefficients to the fewest decimals that keep the error within ``bound``."""
        A = self.design(cols)
        for nd in range(0, 7):
            c = np.round(coef, nd)
            err = float(np.abs(A @ c - self.y).max())
            if err <= bound:
                return c, err
        return coef, float(np.abs(A @ coef - self.y).max())


# ----------------------------------------------------------------------------- spirometry volume

def spirometry_volume_parts(flow: np.ndarray, dt: float, dead_space: float = 0.4,
                            corrected_sign: int = -1) -> tuple[np.ndarray, np.ndarray]:
    """Split LabChart's *Spirometry Volume* into the two running sums it is made of.

    Returns ``(plain, corrected)``: the rectangular integral of the samples that
    are integrated as they are, and that of the samples the volume correction
    divides by the ratio *k*, so that ``volume = plain + corrected / k``.  Which
    samples are corrected does not depend on *k*: the flow of the *corrected
    phase* (the sign given by ``corrected_sign``; expiration in the extension's
    own convention) drains a reservoir of ``dead_space`` litres that is full at
    the record start and is refilled by the other phase (capped at
    ``dead_space``); the first sample whose volume exceeds what is left, and
    every following sample of that phase, is corrected as a whole.
    """
    x = np.asarray(flow, dtype=np.float64) * dt
    n = len(x)
    plain = x.copy()
    corr_inc = np.zeros_like(x)
    if n == 0:
        return plain, corr_inc
    a = np.abs(x)
    phase = np.zeros(n + 2, dtype=np.int8)
    phase[1:n + 1] = (corrected_sign * x) > 0
    d = np.diff(phase)
    starts = np.flatnonzero(d == 1)          # first corrected sample of each run (index in x)
    ends = np.flatnonzero(d == -1)           # one past its last sample
    res = float(dead_space)
    prev_end = 0
    for s, e in zip(starts, ends):
        res = min(dead_space, res + float(a[prev_end:s].sum()))     # the other phase refills the dead space
        cum = np.cumsum(a[s:e])
        j = int(np.searchsorted(cum, res, side="right"))            # first sample that empties the reservoir
        if j < e - s:
            corr_inc[s + j:e] = x[s + j:e]
            plain[s + j:e] = 0.0
            res = 0.0
        else:
            res -= float(cum[-1])
        prev_end = e
    return np.cumsum(plain), np.cumsum(corr_inc)


def spirometry_volume(flow: np.ndarray, dt: float, dead_space: float = 0.4, ratio: float = 1.09,
                      corrected_sign: int = -1) -> np.ndarray:
    """LabChart's *Spirometry Volume* calculation with volume correction.

    A rectangular integral of ``flow`` from the first sample (the volume
    starts at ``flow[0] * dt``, as a record does), where the flow of the
    *corrected phase* is divided by ``ratio`` once the dead-space gas has been
    expired; see :func:`spirometry_volume_parts` for the sample-level rule.
    ADInstruments documents it as ``V = ∫F dt`` (inspiring) and ``V = ∫F/k dt``
    (expiring air in excess of the dead space), with a default dead space of
    400 mL and ``k`` the BTPS factor by default or a value fitted to the data;
    the rule here reproduces LabChart 7's output to float32 precision.
    """
    plain, corrected = spirometry_volume_parts(flow, dt, dead_space, corrected_sign)
    return plain + corrected / ratio


def _fit_spiro_volume(x: np.ndarray, y: np.ndarray, dt: float, q: float, bound: float,
                      source_name: str, source: int) -> Optional[Hypothesis]:
    """Find dead space, ratio and corrected phase of a :func:`spirometry_volume` reproducing ``y``."""
    inc = np.diff(y)
    xs = x[1:] * dt
    valid = np.abs(xs) > 32 * q                       # increments large enough for the ratio to be meaningful
    if valid.sum() < 1000:
        return None

    def evaluate(ds: float, sign: int) -> tuple[float, float]:
        """Best ratio for this dead space (linear least squares) and the resulting error."""
        plain, corrected = spirometry_volume_parts(x, dt, ds, sign)
        cc = float(np.dot(corrected, corrected))
        if cc <= 0:
            return np.inf, 1.0
        inv_k = float(np.dot(corrected, y - plain) / cc)
        if inv_k <= 0:
            return np.inf, 1.0
        k = 1.0 / inv_k
        for cand in (1.09, round(k, 2), round(k, 3), k):            # the roundest ratio within the bound wins
            err = float(np.abs(plain + corrected / cand - y).max())
            if err <= bound:
                return err, cand
        return float(np.abs(plain + corrected / k - y).max()), k

    best: Optional[tuple[float, float, float, int]] = None      # error, dead space, k, sign
    for sign in (-1, 1):
        m = valid & (sign * xs > 0)
        if m.sum() < 500:
            continue
        r = inc[m] / xs[m]
        off = np.abs(r - 1) > 0.02
        if off.sum() < 0.02 * len(r) or (~off).sum() < 0.02 * len(r):
            continue                                  # need both regimes on this phase
        k_est = 1.0 / float(np.median(r[off]))
        if not 0.5 < k_est < 1.5 or abs(k_est - 1) < 0.005:
            continue
        # dead space: the largest volume of one corrected-phase run integrated before the ratio kicked in
        phase = np.flatnonzero(sign * xs > 0)
        cuts = np.flatnonzero(np.diff(phase) > 1) + 1
        ds_est = 0.0
        for run in np.split(phase, cuts):
            if len(run) < 20:
                continue
            rr = np.full(len(run), np.nan)
            v = valid[run]
            rr[v] = inc[run][v] / xs[run][v]
            dev = np.flatnonzero(np.abs(rr - 1) > 0.5 * abs(1 - 1 / k_est))
            if len(dev) == 0:
                continue
            j = int(dev[0])
            later = rr[j:j + 40]
            later = later[~np.isnan(later)]
            if len(later) < 5 or np.median(np.abs(later - 1)) < 0.5 * abs(1 - 1 / k_est):
                continue                              # a stray sample, not the switch
            ds_est = max(ds_est, float(np.abs(xs[run[:j]]).sum()))
        if ds_est <= 0:
            continue
        for ds in dict.fromkeys([0.4, round(ds_est, 1), round(ds_est, 2), round(ds_est, 3), ds_est]):
            err, k = evaluate(ds, sign)
            if best is None or err < best[0]:
                best = (err, ds, k, sign)
            if err <= bound:
                break
        if best is not None and best[0] <= bound:
            break
    if best is None:
        return None
    err, ds, k, sign = best
    if err > bound:                                   # refine the dead space locally before giving up on exactness
        for ds2 in np.linspace(ds * 0.98, ds * 1.02, 81):
            e2, k2 = evaluate(float(ds2), sign)
            if e2 < err:
                err, ds, k = e2, float(ds2), k2
    if err > 100 * bound:
        return None
    phase_name = "negative" if sign < 0 else "positive"
    desc = (f"Spirometry volume of {source_name}: integral reset at record start; {phase_name} flow beyond a "
            f"{ds:g} L dead space divided by k = {k:.6g}")
    note = ("LabChart's Spirometry Volume with volume correction; k = 1.09 is the default (BTPS factor)" if k == 1.09
            else "LabChart's Spirometry Volume with volume correction; k differs from the default 1.09, probably fitted to the data")
    return Hypothesis("spiro_volume", desc, err, bound, (source,), {"dead_space": ds, "ratio": k, "corrected_sign": sign, "dt": dt},
                      note=note)


# ----------------------------------------------------------------------------- spirometry flow

def _fit_spiro_flow(rec: Record, channel: int, source: int, start: int, stop: int, y: np.ndarray,
                    tol: float) -> Optional[Hypothesis]:
    """``y = a * (x - z(t))`` with the zero ``z`` interpolated linearly from the record's first to its last raw sample."""
    n_ticks = rec.ticks
    if n_ticks < 2:
        return None
    xr = rec.read_raw(source, start, stop).astype(np.float64)
    x0 = float(rec.read_raw(source, 0, 1)[0])
    x1 = float(rec.read_raw(source, n_ticks - 1, n_ticks)[0])
    i = np.arange(start, stop, dtype=np.float64)
    z = x0 + (x1 - x0) * i / (n_ticks - 1)
    scale = _phys_scale(rec, source)
    u = (xr - z) * scale
    uu = float(np.dot(u, u))
    if uu <= 0:
        return None
    a = float(np.dot(u, y) / uu)
    if abs(a) < 1e-12:
        return None
    err = float(np.abs(a * u - y).max())
    lsb_err = err / (abs(a) * scale)
    if err > tol and lsb_err > 1.5:
        return None
    yu, xu = _units_label(rec, channel), _units_label(rec, source)
    cal = f"{_fmt(a)} {yu}/{xu}" if yu and xu else _fmt(a)
    desc = (f"Spirometry flow of Ch{source + 1}: {cal}, zero corrected linearly from the record's first "
            f"({x0:g}) to its last ({x1:g}) raw sample")
    return Hypothesis("spiro_flow", desc, err, tol, (source + 1,), {"a": a, "zero_first": x0, "zero_last": x1},
                      lsb_err, lsb_limit=1.5, note="zero drift correction of LabChart's Spirometry Flow")


# ----------------------------------------------------------------------------- moving RMS

def _fit_rms(rec: Record, channel: int, source: int, start: int, stop: int, y_stored: np.ndarray,
             max_window: int) -> Optional[Hypothesis]:
    """Search window length and alignment of a moving RMS of ``source`` (counts) reproducing ``channel``."""
    margin = max_window + 2
    a0 = max(0, start - margin)
    b0 = min(rec.ticks, stop + margin)
    x = rec.read_raw(source, a0, b0).astype(np.float64)
    cs = np.concatenate([[0.0], np.cumsum(x * x)])
    idx = np.arange(start, stop) - a0            # positions of the analysed samples in x
    u_y, u_x = rec.units(channel), rec.units(source)
    # the stored RMS is in the source's count scale when the LSBs agree; otherwise rescale
    scale = 1.0
    if u_y is not None and u_x is not None and u_y.has_lsb and u_x.has_lsb and u_y.lsb != u_x.lsb:
        scale = u_y.lsb / u_x.lsb
    y = y_stored * scale
    if y.max() <= 0:
        return None
    # only positions where any window up to max_window fits (the record edges are handled differently by LabChart)
    ok = (idx >= max_window) & (idx + max_window < len(x))
    if ok.sum() < 200:
        return None
    pos_all = np.flatnonzero(ok)
    sub = pos_all[:: max(1, len(pos_all) // 4000)]
    ys = y[sub]

    def err(w: int, lag: int, positions: np.ndarray, target: np.ndarray) -> float:
        hi = idx[positions] + lag + 1
        lo = hi - w
        m = np.sqrt(np.maximum(cs[hi] - cs[lo], 0) / w)
        return float(np.median(np.abs(m - target) / np.maximum(target, 1e-9)))

    # coarse geometric grid of window lengths, then every length near the best one
    grid = sorted({int(round(2 * 1.03 ** k)) for k in range(int(np.log(max_window / 2) / np.log(1.03)) + 2)} | {2, max_window})
    best = (np.inf, 0, 0)
    for w in grid:
        if 2 <= w <= max_window:
            e = err(w, w // 2, sub, ys)
            if e < best[0]:
                best = (e, w, w // 2)
    if best[0] > 0.1:
        return None
    wc = best[1]
    for w in range(max(2, int(wc / 1.06) - 1), min(max_window, int(wc * 1.06) + 2)):
        e = err(w, w // 2, sub, ys)
        if e < best[0]:
            best = (e, w, w // 2)
    if best[0] > 0.05:
        return None
    _, w0, _ = best
    for w in range(max(2, w0 - 3), w0 + 4):
        for lag in range(max(0, w // 2 - 4), min(w, w // 2 + 5)):
            e = err(w, lag, sub, ys)
            if e < best[0]:
                best = (e, w, lag)
    e, w, lag = best
    hi = idx[pos_all] + lag + 1
    lo = hi - w
    m = np.sqrt(np.maximum(cs[hi] - cs[lo], 0) / w) / scale
    max_err_phys = float(np.abs(m - y_stored[pos_all]).max()) * _phys_scale(rec, channel)
    tol = 8 * _quantisation(rec, channel, y_stored)
    after = lag                      # the window of sample i covers i-before .. i+after
    before = w - 1 - lag
    ms = 1000.0 * w / rec.sample_rate_hz(source)
    if after > 0:
        desc = f"RMS of Ch{source + 1}, window {w} samples ({ms:g} ms), centred: {before} before, {after} after"
    else:
        desc = f"RMS of Ch{source + 1}, window {w} samples ({ms:g} ms), trailing"
    return Hypothesis("rms", desc, max_err_phys, tol, (source + 1,), {"window": w, "before": before, "after": after},
                      note=f"median relative error {e:.2g}; record edges not compared")


# ----------------------------------------------------------------------------- driver

def infer_channel(rec: Record, channel: int, start: int = 0, length: int = 200_000,
                  sources: Optional[Sequence[int]] = None, rms_max_window: int = 4000) -> list[Hypothesis]:
    """Test the standard calculations for ``channel`` against the other channels
    of ``rec`` over ticks ``[start, start+length)``; best hypotheses first."""
    f: AdichtFile = rec.file
    n_ch = f.n_channels
    stop = min(rec.ticks, start + length)
    if stop - start < 100:
        return []
    y_stored = rec.read_raw(channel, start, stop).astype(np.float64)
    y = rec.convert(channel, y_stored)
    q = _quantisation(rec, channel, y_stored)
    tol = 8 * q
    names = {c: f"Ch{c + 1}" for c in range(n_ch)}
    others = [c for c in (sources if sources is not None else range(n_ch)) if c != channel]
    phys = {c: rec.read(c, start, stop) for c in others}
    is_raw = {c: f.index.channel_dtype(c) == "int16" for c in others}
    raw_lsb = {c: (_phys_scale(rec, c) if is_raw[c] else 0.0) for c in others}
    fitter = _Fitter(y, max(1, (stop - start) // 25_000))
    hyps: list[Hypothesis] = []
    dt = 1.0 / rec.tick_rate_hz

    # -- integral of another channel (reset at record start), plain or with spirometry volume correction --
    if start == 0 and abs(y[0]) <= max(tol, 1e-3 * np.abs(y).max()):
        bound = 16 * q + tol                          # float32 accumulation over the record
        for c, x in phys.items():
            cum_err = float(np.abs(np.cumsum(x) * dt - y).max())
            if cum_err <= bound:
                inc_err = float(np.abs(np.diff(y) - x[1:] * dt).max())
                hyps.append(Hypothesis("integral", f"Integral of {names[c]} (reset at record start)", cum_err,
                                       bound, (c + 1,), {"dt": dt}, note=f"increment error {inc_err:.2g}"))
            elif _corr(np.diff(y), x[1:]) > 0.9:          # the increments follow x: a corrected integral?
                h = _fit_spiro_volume(x, y, dt, q, bound, names[c], c + 1)
                if h is not None:
                    hyps.append(h)

    # -- linear in one channel --------------------------------------------------------
    for c, x in phys.items():
        if np.ptp(x) == 0:
            continue
        coef, err = fitter.fit([x])
        if abs(coef[0]) < 1e-12:
            continue
        lsb_err = err / (abs(coef[0]) * raw_lsb[c]) if raw_lsb[c] else None
        if err <= tol:
            (a, b), err = fitter.snap([x], coef, tol)
            hyps.append(Hypothesis("arithmetic", _lin_desc([(a, names[c])], b, tol), err, tol, (c + 1,), {"a": a, "b": b}, lsb_err))
        elif lsb_err is not None and lsb_err <= 1.05:
            (a, b), err = fitter.snap([x], coef, 1.05 * abs(coef[0]) * raw_lsb[c])
            hyps.append(Hypothesis("copy", _lin_desc([(a, names[c])], b, 0.0), err, tol, (c + 1,), {"a": a, "b": b},
                                   err / (abs(a) * raw_lsb[c]), note="the calculation used a finer version of the source than the stored counts"))
        elif is_raw[c]:
            h = _fit_spiro_flow(rec, channel, c, start, stop, y, tol)
            if h is not None:
                hyps.append(h)

    # -- positive / negative parts of one channel ---------------------------------------
    for c, x in phys.items():
        if x.min() >= 0 or x.max() <= 0:
            continue
        pos, neg = (x + np.abs(x)) / 2, (x - np.abs(x)) / 2
        coef, err = fitter.fit([pos, neg])
        if err <= tol and (abs(coef[0]) > 1e-9 or abs(coef[1]) > 1e-9):
            (a, b, k), err = fitter.snap([pos, neg], coef, tol)
            if abs(a - 1) < 1e-9 and abs(b) < 1e-9 and abs(k) <= tol:
                desc = f"({names[c]}+Abs({names[c]}))/2"
            elif abs(b - 1) < 1e-9 and abs(a) < 1e-9 and abs(k) <= tol:
                desc = f"({names[c]}-Abs({names[c]}))/2"
            else:
                desc = _lin_desc([(a, f"pos({names[c]})"), (b, f"neg({names[c]})")], k, tol)
            hyps.append(Hypothesis("rectify", desc, err, tol, (c + 1,), {"pos": a, "neg": b, "const": k}))
            continue
        # the same with the channel shifted first, LabChart's ``(Ch+c±Abs(Ch+c))/2``: where the
        # result is not zero it equals x+c, which gives c
        active = np.abs(y) > tol
        if 100 < active.sum() < len(y) - 100:
            d = (y - x)[active]
            c0 = float(np.median(d))
            if np.abs(d - c0).max() <= 4 * tol + 1e-9 * abs(c0):
                for shift in dict.fromkeys([round(c0, 2), round(c0, 3), round(c0, 4), c0]):
                    xs = x + shift
                    for sign_name, cand in (("-", (xs - np.abs(xs)) / 2), ("+", (xs + np.abs(xs)) / 2)):
                        err = float(np.abs(cand - y).max())
                        if err <= tol:
                            term = f"{names[c]}{'+' if shift >= 0 else '-'}{_fmt(abs(shift))}"
                            hyps.append(Hypothesis("rectify", f"({term}{sign_name}Abs({term}))/2", err, tol, (c + 1,),
                                                   {"pos": 1.0 if sign_name == "+" else 0.0, "neg": 1.0 if sign_name == "-" else 0.0,
                                                    "const": 0.0, "shift": shift}))
                            break
                    else:
                        continue
                    break

    # -- linear in two channels ---------------------------------------------------------
    cand = [c for c in others if np.ptp(phys[c]) > 0]
    for i, c1 in enumerate(cand):
        for c2 in cand[i + 1:]:
            coef, err = fitter.fit([phys[c1], phys[c2]])
            if abs(coef[0]) < 1e-9 or abs(coef[1]) < 1e-9:
                continue
            lsb_span = abs(coef[0]) * raw_lsb[c1] + abs(coef[1]) * raw_lsb[c2]
            lsb_err = err / lsb_span if (raw_lsb[c1] and raw_lsb[c2]) else None
            if err <= tol:
                (a, b, k), err = fitter.snap([phys[c1], phys[c2]], coef, tol)
                note = ""
            elif lsb_err is not None and lsb_err <= 1.05:
                (a, b, k), err = fitter.snap([phys[c1], phys[c2]], coef, 1.05 * lsb_span)
                lsb_err = err / lsb_span
                note = "the calculation used a finer version of the inputs than the stored counts"
            else:
                continue
            hyps.append(Hypothesis("arithmetic", _lin_desc([(a, names[c1]), (b, names[c2])], k, tol), err, tol,
                                   (c1 + 1, c2 + 1), {"a": a, "b": b, "c": k}, lsb_err, note=note))

    # -- moving RMS of a raw channel ------------------------------------------------------
    if y.min() >= 0:
        for c in others:
            if not is_raw[c]:
                continue
            h = _fit_rms(rec, channel, c, start, stop, y_stored, rms_max_window)
            if h is not None:
                hyps.append(h)

    def rank(h: Hypothesis):
        later = any(s > channel + 1 for s in h.sources)        # explained by channels defined after it: probably circular
        tier = (0 if h.exact else 1 if h.within_lsb else 3) + (2 if later else 0)
        return (tier, len(h.sources), h.max_abs_error / max(h.tolerance, 1e-300), max(h.sources))
    hyps.sort(key=rank)
    return hyps


def infer_record(rec: Record, channels: Optional[Sequence[int]] = None, start: int = 0, length: int = 200_000,
                 only_float: bool = True) -> dict[int, list[Hypothesis]]:
    """Run :func:`infer_channel` for every calculated (float32) channel of a record."""
    f = rec.file
    targets = list(channels) if channels is not None else [c for c in range(f.n_channels)
                                                           if not only_float or f.index.channel_dtype(c) == "float32"]
    return {c: infer_channel(rec, c, start, length) for c in targets}
