"""Point-aligned yield close spreads only. Inputs are percentage points."""
import pandas as pd


def curve_closes(yields):
    required = ("US02Y", "US05Y", "US10Y", "US30Y")
    if set(required) - set(yields):
        raise ValueError("Need 2Y, 5Y, 10Y, 30Y series")
    for key in required:
        s = yields[key]
        if not isinstance(s.index, pd.DatetimeIndex) or s.index.tz is None or s.index.has_duplicates:
            raise ValueError("Yield series require unique timezone-aware timestamps")
    aligned = pd.concat([yields[k].rename(k) for k in required], axis=1, join="inner").dropna().sort_index()
    for name, long, short in (("2s10s", "US10Y", "US02Y"), ("2s30s", "US30Y", "US02Y"),
                              ("5s30s", "US30Y", "US05Y"), ("10s30s", "US30Y", "US10Y")):
        aligned[name + "_bp"] = (aligned[long] - aligned[short]) * 100
    return aligned
