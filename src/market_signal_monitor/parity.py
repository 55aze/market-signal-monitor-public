"""Compare TV-exported reference against Python, with exact alignment first."""
import numpy as np

SIGNALS = ("DXDX", "DBJGXC")
BOTTOM_DEPENDENCY_ORDER = (
    "D", "A", "M", "N1", "MM1", "CC1", "CC2", "CC3",
    "DIFL1", "DIFL2", "DIFL3", "AAA", "BBB", "CCC", "JJJ", "DXDX",
)
TOP_DEPENDENCY_ORDER = (
    "D", "A", "M", "N1", "MM1", "CH1", "CH2", "CH3",
    "DIFH1", "DIFH2", "DIFH3", "ZJDBL", "GXDBL", "DBBL", "DBJG", "DBJGXC",
)
DEPENDENCY_ORDER = {"DXDX": BOTTOM_DEPENDENCY_ORDER, "DBJGXC": TOP_DEPENDENCY_ORDER}


def _value(frame, t, col):
    if col not in frame or t not in frame.index:
        return None
    value = frame.at[t, col]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value


def _equal(a, b, atol, rtol):
    if a is None or b is None:
        return None
    return bool(np.isclose(a, b, atol=atol, rtol=rtol, equal_nan=True))


def _first_dependency_divergence(reference, candidate, signal, times, atol, rtol):
    diagnostics = []
    for t in sorted(times):
        first = None
        chain = []
        for col in DEPENDENCY_ORDER[signal]:
            a, b = _value(reference, t, col), _value(candidate, t, col)
            eq = _equal(a, b, atol, rtol)
            item = {"column": col, "tv": a, "python": b, "equal": eq}
            chain.append(item)
            if first is None and eq is False:
                first = item
        diagnostics.append({"time": str(t), "first_divergence": first, "chain": chain})
    return diagnostics


def compare(reference, candidate, warmup=500, atol=1e-8, rtol=1e-10):
    if warmup < 0:
        raise ValueError("warmup must be nonnegative")
    for signal in SIGNALS:
        if signal not in reference or signal not in candidate:
            raise ValueError(f"Missing exported signal: {signal}")
        for frame in (reference, candidate):
            values = frame[signal].dropna()
            if not values.isin([-1, 0, 1]).all():
                raise ValueError(f"{signal} must be -1 (na), 0 or 1")
    if len(reference) <= warmup:
        raise ValueError("No evaluation bars after warmup")
    ref = reference.iloc[warmup:]
    if "bar_confirmed" in ref:
        ref = ref[ref.bar_confirmed == 1]
    else:
        ref = ref.iloc[:-1]  # latest chart row may still be forming
    if ref.empty:
        raise ValueError("No closed evaluation bars")
    cand = candidate.loc[(candidate.index >= ref.index[0]) & (candidate.index <= ref.index[-1])]
    if "close_status" in cand:
        cand = cand[cand.close_status != "unknown_latest_bar"]
    common = ref.index.intersection(cand.index)
    missing = ref.index.difference(cand.index)
    extra = cand.index.difference(ref.index)
    result = {"evaluation_start": str(ref.index[0]), "evaluation_end": str(ref.index[-1]),
              "warmup_bars_excluded": warmup, "reference_bars": len(ref),
              "candidate_bars": len(cand), "common_bars": len(common),
              "missing_candidate_times": [str(x) for x in missing],
              "extra_candidate_times": [str(x) for x in extra],
              "numeric_tolerance": {"atol": atol, "rtol": rtol}, "signals": {}, "columns": {}}
    union = ref.index.union(cand.index).sort_values()
    positions = {t: i for i, t in enumerate(union)}
    signal_mismatch_times = {}
    for name in SIGNALS:
        actual = set(cand.index[cand[name] == 1])
        expected = set(ref.index[ref[name] == 1])
        exact = actual & expected
        fp, fn = actual - expected, expected - actual
        signal_mismatch_times[name] = fp | fn
        available = set(fn)
        offsets = []
        for t in sorted(fp):
            options = [x for x in available if abs(positions[t] - positions[x]) <= 1]
            if options:
                match = min(options, key=lambda x: (abs(positions[t] - positions[x]), x))
                available.remove(match)
                offsets.append({"python_time": str(t), "tv_time": str(match),
                                "bar_offset": positions[t] - positions[match]})
        result["signals"][name] = {
            "tv_events": len(expected), "python_events": len(actual), "exact_matches": len(exact),
            "precision_exact": len(exact) / len(actual) if actual else None,
            "recall_exact": len(exact) / len(expected) if expected else None,
            "python_only": [str(x) for x in sorted(fp)], "tv_only": [str(x) for x in sorted(fn)],
            "offset_diagnostics_only": offsets}
    shared = [c for c in ref.columns if c in cand.columns and c not in
              ("symbol", "close_status", "bar_confirmed", "first_time", "time_close")]
    for col in shared:
        a = ref.loc[common, col].to_numpy(dtype=float)
        b = cand.loc[common, col].to_numpy(dtype=float)
        if col in SIGNALS:
            a = np.where(a == -1, np.nan, a)
            b = np.where(b == -1, np.nan, b)
        eq = np.isclose(a, b, atol=atol, rtol=rtol, equal_nan=True)
        finite = np.isfinite(a) & np.isfinite(b)
        result["columns"][col] = {
            "mismatched_bars": int((~eq).sum()),
            "max_abs_error": float(np.max(np.abs(a[finite]-b[finite]))) if finite.any() else None,
            "first_mismatch": str(common[np.flatnonzero(~eq)[0]]) if (~eq).any() else None}
    result["dependency_diagnostics"] = {
        signal: _first_dependency_divergence(ref, cand, signal,
                                             {t for t in times if t in ref.index and t in cand.index},
                                             atol, rtol)
        for signal, times in signal_mismatch_times.items()
    }
    mismatch = bool(len(missing) or len(extra)) or any(v["mismatched_bars"] for v in result["columns"].values())
    mismatch |= any(v["python_only"] or v["tv_only"] for v in result["signals"].values())
    enough = all(v["tv_events"] > 0 and v["python_events"] > 0 for v in result["signals"].values())
    result["status"] = "mismatch" if mismatch else ("match_on_supplied_sample" if enough else "inconclusive_no_events")
    result["production_validated"] = False
    result["seed_note"] = "Warmup is a diagnostic exclusion, not proof of equal EMA initialization. Compare D/A and extrema."
    if "first_time" in reference:
        result["tv_first_calculation_time_ms"] = float(reference.first_time.iloc[0])
        result["candidate_first_input_time_ms"] = candidate.index[0].timestamp() * 1000
    return result
