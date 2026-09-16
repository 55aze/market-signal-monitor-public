"""History evidence, separate from the unchanged Pine calculation.

No fixed bar count proves EMA accuracy. Seed weights quantify residual initial-value
influence, not error bounds. Signal masks require observed crossing boundaries for
all nested extrema and both previous CCC/DBBL evaluations used by the edge detector.
"""
import numpy as np
import pandas as pd

POLICY = 'dependency-v1'


def dependency_masks(calculated):
    m = calculated['M'].to_numpy(dtype=float)
    n = len(m)
    down = np.zeros(n, dtype=bool)
    up = np.zeros(n, dtype=bool)
    down[1:] = (m[:-1] >= 0) & (m[1:] < 0)
    up[1:] = (m[:-1] <= 0) & (m[1:] > 0)
    last_down = np.maximum.accumulate(np.where(down, np.arange(n), -1))
    last_up = np.maximum.accumulate(np.where(up, np.arange(n), -1))
    masks = {}
    for signal, boundary, opposite, columns in (
        ('DXDX', last_down, last_up, ('CC1', 'CC2', 'CC3', 'DIFL1', 'DIFL2', 'DIFL3')),
        ('DBJGXC', last_up, last_down, ('CH1', 'CH2', 'CH3', 'DIFH1', 'DIFH2', 'DIFH3')),
    ):
        primitive = np.zeros(n, dtype=bool)
        for i in range(n):
            j = i
            valid = True
            # CC1(i), CC1(i-MM1(i)-1), then CC1(j-MM1(j)-1);
            # CH1 uses N1 instead. Boundaries must actually have been observed.
            for depth in range(3):
                if j < 0 or boundary[j] < 0:
                    valid = False
                    break
                if depth < 2:
                    if opposite[j] < 0:
                        valid = False
                        break
                    j = opposite[j] - 1
            primitive[i] = valid
        finite = np.isfinite(calculated[list(columns)].to_numpy(dtype=float)).all(axis=1)
        primitive &= finite
        ready = np.zeros(n, dtype=bool)
        ready[2:] = primitive[1:-1] & primitive[:-2]
        ready &= np.isfinite(calculated['D'].to_numpy(dtype=float))
        masks[signal] = pd.Series(ready, index=calculated.index)
    return pd.DataFrame(masks, index=calculated.index)


def history_evidence(bars, calculated, masks, latest, requested):
    i = bars.index.get_loc(latest)
    row = calculated.loc[latest]
    fields = ('ema20', 'ema50', 'ema100', 'ema200', 'blueUpperBand',
              'blueLowerBand', 'yellowUpperBand', 'yellowLowerBand')
    return {
        'policy': POLICY, 'requested_bars': requested, 'returned_bars': len(bars),
        'history_start': bars.index[0].isoformat(), 'history_end': bars.index[-1].isoformat(),
        'confirmed_history_bars': i + 1,
        'full_listing_history_verified': False,
        'structure_computable': bool(np.isfinite(row[list(fields)].to_numpy(dtype=float)).all()),
        'ema_initial_seed_weight': {str(n): float(((n - 1) / (n + 1)) ** i)
                                    for n in (20, 26, 50, 89, 100, 200)},
        'ema_accuracy': 'not_certified; seed weight is not an error bound',
        'signal_dependencies_ready': {key: bool(masks.loc[latest, key]) for key in masks},
        'first_dependency_ready_bar': {
            key: (masks.index[masks[key]][0].isoformat() if masks[key].any() else None)
            for key in masks},
        'signal_scope': 'observed crossing dependencies only; not TradingView parity certification',
    }
