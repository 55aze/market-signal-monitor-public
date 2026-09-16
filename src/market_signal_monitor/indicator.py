"""Explicit transcription of source.pine. Names and formula structure retained.

& / | / ~ implement Pine and / or / not; [] is per-bar history, not slicing.
No regime filters, debouncing, rounding, or redesigned signals are applied.
"""
import pandas as pd
from .pine_ops import Series, ema, barssince, nz, lowest, highest, count_condition, where


def calculate(bars: pd.DataFrame, n1: int = 26, n2: int = 89) -> pd.DataFrame:
    close, high, low = (Series(bars[k].to_numpy()) for k in ("close", "high", "low"))
    D = ema(close, 12) - ema(close, 26)
    A = ema(D, 9)
    M = (D - A) * 2
    N1 = nz(barssince((M[1] >= 0) & (M < 0)), 0)
    MM1 = nz(barssince((M[1] <= 0) & (M > 0)), 0)
    CC1 = lowest(close, N1 + 1)
    CC2 = CC1[MM1 + 1]
    CC3 = CC2[MM1 + 1]
    DIFL1 = lowest(D, N1 + 1)
    DIFL2 = DIFL1[MM1 + 1]
    DIFL3 = DIFL2[MM1 + 1]
    CH1 = highest(close, MM1 + 1)
    CH2 = CH1[N1 + 1]
    CH3 = CH2[N1 + 1]
    DIFH1 = highest(D, MM1 + 1)
    DIFH2 = DIFH1[N1 + 1]
    DIFH3 = DIFH2[N1 + 1]
    AAA = (CC1 < CC2) & (DIFL1 > DIFL2) & (M[1] < 0) & (D < 0)
    BBB = (CC1 < CC3) & (DIFL1 < DIFL2) & (DIFL1 > DIFL3) & (M[1] < 0) & (D < 0)
    CCC = (AAA | BBB) & (D < 0)
    LLL = (~CCC[1]) & CCC
    XXX = ((AAA[1] & (DIFL1 <= DIFL2) & (D < A)) |
           (BBB[1] & (DIFL1 <= DIFL3) & (D < A)))
    JJJ = CCC[1] & (abs(D[1]) >= (abs(D) * 1.01))
    BLBL = JJJ[1] & CCC & ((abs(D[1]) * 1.01) <= abs(D))
    DXDX = (~JJJ[1]) & JJJ
    DJGXX = ((close < CC2) | (close < CC1)) & ((JJJ[MM1 + 1] | JJJ[MM1]) &
             (~LLL[1]) & (count_condition(JJJ, 24) >= 1))
    DJXX = (~(count_condition(DJGXX[1], 2) >= 1)) & DJGXX
    DXX = (XXX | DJXX) & ~CCC
    ZJDBL = (CH1 > CH2) & (DIFH1 < DIFH2) & (M[1] > 0) & (D > 0)
    GXDBL = (CH1 > CH3) & (DIFH1 > DIFH2) & (DIFH1 < DIFH3) & (M[1] > 0) & (D > 0)
    DBBL = (ZJDBL | GXDBL) & (D > 0)
    DBL = (~DBBL[1]) & (DBBL & (D > A))
    DBLXS = ((ZJDBL[1] & (DIFH1 >= DIFH2) & (D > A)) |
             (GXDBL[1] & (DIFH1 >= DIFH3) & (D > A)))
    DBJG = DBBL[1] & (D[1] >= (D * 1.01))
    DBJGXC = (~DBJG[1]) & DBJG
    DBJGBL = DBJG[1] & (DBBL & ((D[1] * 1.01) <= D))
    ZZZZZ = ((close > CH2) | (close > CH1)) & ((DBJG[N1 + 1] | DBJG[N1]) &
             (~DBL[1]) & (count_condition(DBJG, 23) >= 1))
    YYYYY = (~(count_condition(ZZZZZ[1], 2) >= 1)) & ZZZZZ
    WWWWW = (DBLXS | YYYYY) & ~DBBL
    ema20, ema50, ema100, ema200 = (ema(close, n) for n in (20, 50, 100, 200))
    blueUpperBand = ema(high, n1)
    blueLowerBand = ema(low, n1)
    yellowUpperBand = ema(high, n2)
    yellowLowerBand = ema(low, n2)
    blueUpperLine = where(close > blueUpperBand, blueUpperBand)
    blueLowerLine = where(close < blueLowerBand, blueLowerBand)
    blueMidLine = where((close > blueLowerBand) & (close < blueUpperBand),
                        (blueUpperBand + blueLowerBand) * 0.5)
    yellowUpperLine = where(close > yellowUpperBand, yellowUpperBand)
    yellowLowerLine = where(close < yellowLowerBand, yellowLowerBand)
    yellowMidLine = where((close > yellowLowerBand) & (close < yellowUpperBand),
                          (yellowUpperBand + yellowLowerBand) * 0.5)
    values = {k: v.v for k, v in locals().items() if isinstance(v, Series)}
    return pd.DataFrame(values, index=bars.index)
