"""Descriptive snapshot of one signal bar; no signal or identity changes."""
import math

PERIODS = (20, 50, 100, 200)
BANDS = ("blueUpperBand", "blueLowerBand", "yellowUpperBand", "yellowLowerBand")


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def snapshot(close, row):
    values = {"Close": finite(close), **{f"EMA{n}": finite(row.get(f"ema{n}")) for n in PERIODS},
              **{k: finite(row.get(k)) for k in BANDS}}
    close = values["Close"]
    ordered = {k: values[k] for k in ("Close", "EMA20", "EMA50", "EMA100", "EMA200")}
    ranking = None
    if all(v is not None for v in ordered.values()):
        groups = {}
        for k, v in ordered.items():
            groups.setdefault(v, []).append(k)
        ranking = " > ".join(" = ".join(groups[v]) for v in sorted(groups, reverse=True))
    ema = [values[f"EMA{n}"] for n in PERIODS]
    alignment = "Unavailable"
    if all(v is not None for v in ema):
        alignment = ("Bullish" if all(a > b for a, b in zip(ema, ema[1:])) else
                     "Bearish" if all(a < b for a, b in zip(ema, ema[1:])) else "Mixed")
    positions, distances = {}, {}
    for n in PERIODS:
        value = values[f"EMA{n}"]
        positions[str(n)] = ("Unavailable" if close is None or value is None else
                             "Above" if close > value else "Below" if close < value else "Equal")
        distances[str(n)] = (None if close is None or value in (None, 0) else
                             finite((close / value - 1) * 100))
    channels = {}
    for color in ("blue", "yellow"):
        upper, lower = values[color + "UpperBand"], values[color + "LowerBand"]
        channels[color] = ("Unavailable" if None in (close, upper, lower) or upper < lower else
                           "Above" if close > upper else "Below" if close < lower else "Inside")
    return {"version": 1, "values": values, "order": ranking, "alignment": alignment,
            "ema_position": positions, "distance_pct": distances, "channel_position": channels}


NUMBER_FIELDS = {**{f"EMA{n}": f"EMA {n}" for n in PERIODS},
                 "blueUpperBand": "Blue Upper", "blueLowerBand": "Blue Lower",
                 "yellowUpperBand": "Yellow Upper", "yellowLowerBand": "Yellow Lower"}


def notion_properties(s):
    props = {name: {"number": s["values"][key]} for key, name in NUMBER_FIELDS.items()}
    props["EMA Order"] = {"rich_text": [{"type": "text", "text": {"content": s["order"] or "Unavailable"}}]}
    props["EMA Alignment"] = {"select": {"name": s["alignment"]}}
    for n in PERIODS:
        props[f"Close vs EMA {n}"] = {"select": {"name": s["ema_position"][str(n)]}}
        # Number stores percentage points: 5 means 5%, not Notion's percent format.
        props[f"Distance EMA {n} %"] = {"number": s["distance_pct"][str(n)]}
    for color in ("blue", "yellow"):
        props[color.title() + " Position"] = {"select": {"name": s["channel_position"][color]}}
    return props


SCHEMA = {**{name: "number" for name in NUMBER_FIELDS.values()}, "EMA Order": "rich_text",
          "EMA Alignment": "select", "Blue Position": "select", "Yellow Position": "select",
          **{f"Close vs EMA {n}": "select" for n in PERIODS},
          **{f"Distance EMA {n} %": "number" for n in PERIODS}}
