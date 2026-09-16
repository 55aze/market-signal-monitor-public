"""Generate a diagnostic Pine copy; never alter source.pine."""
import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
source = (ROOT / "pine/source.pine").read_text(encoding="utf-8")
compute = source.split("// ==================== 绘图部分 ====================")[0]
compute = compute.replace('indicator("抄底化出信号 + 蓝黄隧道", overlay=true, max_bars_back=1000)',
                          'indicator("DXDX parity export v1", overlay=false, max_bars_back=1000, precision=12)')
names = re.findall(r"^([A-Z][A-Z0-9]*) =", compute, flags=re.M)
booleans = set(names[names.index("AAA"):])
names += ["ema20", "ema50", "ema100", "ema200", "blueUpperBand", "blueLowerBand",
          "yellowUpperBand", "yellowLowerBand"]
compute += '\n// Diagnostic outputs only: na booleans remain -1, false=0, true=1.\n'
for name in names:
    value = f"na({name}) ? -1 : {name} ? 1 : 0" if name in booleans else name
    compute += f'plot({value}, title="{name}")\n'
compute += 'plot(barstate.isconfirmed ? 1 : 0, title="bar_confirmed")\n'
compute += 'plot(time_close, title="time_close")\n'
compute += 'plot(ta.valuewhen(barstate.isfirst, time, 0), title="first_time")\n'
if len(names) + 3 > 64:
    raise RuntimeError("Pine plot budget exceeded")
(ROOT / "pine/parity_export.pine").write_text(compute, encoding="utf-8")
(ROOT / "pine/source.sha256").write_text(hashlib.sha256(source.encode()).hexdigest() + "  source.pine\n")
print(f"Generated parity_export.pine with {len(names) + 3} plots; original source preserved")
