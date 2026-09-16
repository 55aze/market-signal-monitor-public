"""Small, explicit subset of Pine v5 series operations used by source.pine.

Numerical/comparison na is retained. Logical operators cast boolean na to
false per Pine v5 type-system documentation; this is NOT SQL/Kleene logic.
Each array contains every historical evaluation of one Pine variable.
"""
import operator
import numpy as np


class Series:
    def __init__(self, values):
        self.v = np.asarray(values, dtype=float)

    def _bin(self, other, op, comparison=False):
        b = other.v if isinstance(other, Series) else other
        out = op(self.v, b).astype(float)
        if comparison:
            out = np.where(np.isnan(self.v) | np.isnan(b), np.nan, out)
        return Series(out)

    def __add__(self, x): return self._bin(x, operator.add)
    def __sub__(self, x): return self._bin(x, operator.sub)
    def __mul__(self, x): return self._bin(x, operator.mul)
    def __lt__(self, x): return self._bin(x, operator.lt, True)
    def __le__(self, x): return self._bin(x, operator.le, True)
    def __gt__(self, x): return self._bin(x, operator.gt, True)
    def __ge__(self, x): return self._bin(x, operator.ge, True)
    def __abs__(self): return Series(np.abs(self.v))
    def __bool__(self): raise TypeError("Use &, |, ~ for Pine series; never Python and/or/not")
    def __and__(self, other): return Series(truth(self) & truth(other))
    def __or__(self, other): return Series(truth(self) | truth(other))
    def __invert__(self): return Series(~truth(self))

    def __getitem__(self, offset):
        offsets = offset.v if isinstance(offset, Series) else np.full(len(self.v), offset)
        if np.any(~np.isfinite(offsets)) or np.any(offsets < 0) or np.any(offsets != np.floor(offsets)):
            raise ValueError("History offsets must be finite nonnegative integers")
        # Respect the source's declared history-buffer contract; never clip.
        if np.any(offsets > 1000):
            raise ValueError("History offset exceeds source max_bars_back=1000; review in TV")
        indices = np.arange(len(self.v)) - offsets.astype(int)
        out = np.full(len(self.v), np.nan)
        valid = indices >= 0
        out[valid] = self.v[indices[valid]]
        return Series(out)


def truth(s):
    return np.isfinite(s.v) & (s.v != 0)


def nz(s, value=0):
    return Series(np.where(np.isnan(s.v), value, s.v))


def ema(s, length):
    if not isinstance(length, int) or length < 1:
        raise ValueError("EMA length must be a positive integer")
    alpha = 2.0 / (length + 1)
    prev = np.nan
    out = []
    for value in s.v:
        if not np.isnan(value):
            prev = value if np.isnan(prev) else alpha * value + (1 - alpha) * prev
        out.append(prev)
    return Series(out)


def barssince(condition):
    last = None
    out = []
    for i, event in enumerate(truth(condition)):
        if event:
            last = i
        out.append(np.nan if last is None else i - last)
    return Series(out)


def extreme(s, lengths, which):
    if np.any(lengths.v < 1) or np.any(lengths.v != np.floor(lengths.v)):
        raise ValueError("Extremum window must be positive integral")
    if np.any(lengths.v > 1000):
        raise ValueError("Window exceeds source max_bars_back=1000; review in TV")
    out = []
    for i, length in enumerate(lengths.v.astype(int)):
        window = s.v[max(0, i - length + 1):i + 1]
        window = window[~np.isnan(window)]
        out.append(which(window) if len(window) else np.nan)
    return Series(out)


def lowest(s, lengths): return extreme(s, lengths, np.min)
def highest(s, lengths): return extreme(s, lengths, np.max)


def count_condition(s, length):
    # Pine condition ? 1 : 0 maps na to zero BEFORE math.sum.
    values = truth(s).astype(float)
    out = np.full(len(values), np.nan)
    if len(values) >= length:
        out[length - 1:] = np.convolve(values, np.ones(length), mode="valid")
    return Series(out)


def where(condition, yes, no=np.nan):
    return Series(np.where(truth(condition), yes.v if isinstance(yes, Series) else yes,
                           no.v if isinstance(no, Series) else no))
