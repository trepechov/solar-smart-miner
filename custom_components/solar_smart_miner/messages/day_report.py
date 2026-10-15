"""Message rule 6, the day report: one calendar day, closed after midnight (message.day-report).

When a new date begins, DayMessages keeps the day that ended (its totals, made into the report's
figures by `build_report`) as not yet reported. This rule sends the newest kept day that isn't
reported yet, so a poll that fails, or HA down at midnight, only delays it. The figures and the
words are computed (`format_text`), never written by the AI. scripts/farm_day.py, the
prototype, builds the same figures from the farm's recorder history and formats them here too.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime
import math

from .context import DayContext
from .totals import SLICE_MIN, close_slice

NAME = "day-report"
TEST_LINE = "🧪 TEST MESSAGE: daily report preview, please ignore"
KEPT_DAYS = 7  # finished days kept for a report on request


def check(ctx: DayContext, state: dict) -> str | None:
    kept = state["kept_days"]
    if not kept or kept[-1]["reported"]:
        return None
    kept[-1]["reported"] = True
    return format_text(kept[-1]["report"])


# --- the figures ----------------------------------------------------------------


def _hhmm(iso: str | None) -> str | None:
    return datetime.fromisoformat(iso).strftime("%H:%M") if iso else None


def _grid_window(h_mgrid: list[float], width: int = 3) -> tuple[str, str] | None:
    """The `width` consecutive hours in which the grid gave the miners the most, when it gave any."""
    if sum(h_mgrid) < 0.05:
        return None
    best = max(range(24 - width + 1), key=lambda h: sum(h_mgrid[h:h + width]))
    return f"{best:02d}:00", f"{best + width:02d}:00"


def build_report(state: dict, *, incomplete: bool = False) -> dict:
    """The day's figures as the template reads them (the prototype's shape). Leaves the
    totals as they are, so it works for today so far too."""
    totals = state["totals"]
    best = totals["best"]
    if totals["slice"] is not None and (best is None or totals["slice_kwh"] > best["kwh"]):
        best = {"slice": totals["slice"], "kwh": totals["slice_kwh"]}
    solar, imp, exp = totals["solar_kwh"], totals["import_kwh"], totals["export_kwh"]
    miners = totals["miners_kwh"]
    miners_solar = totals["miners_solar_kwh"]
    miners_grid = miners - miners_solar
    house = solar + imp - exp - miners
    house_grid = max(0.0, imp - miners_grid)
    commands = state["commands"]
    kinds = Counter(c["kind"] for c in commands)
    miner_out = []
    for name, m in totals["miners"].items():
        th = m["th"]
        miner_out.append({
            "name": name,
            "kwh": m["kwh"],
            "hours": m["mining_s"] / 3600,
            "avg_th_s": th / m["mining_s"] if m["mining_s"] else 0.0,
            "hashes": th * 1e12,
            "j_per_th": m["kwh"] * 3.6e6 / th if th else None,
            "starts": [_hhmm(c["at"]) for c in commands if c["miner"] == name and c["kind"] == "start"],
        })
    window = _grid_window(totals["miners_grid_h"])
    th_total = sum(m["hashes"] for m in miner_out) / 1e12
    day = date.fromisoformat(state["date"])
    light = [_hhmm(totals["first_light"]), _hhmm(totals["last_light"])] if totals["first_light"] else None
    return {
        "date": state["date"],
        "day_label": f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}",
        "totals": {
            "solar": solar, "miners": miners, "import": imp, "export": exp, "house": house,
            "miners_solar": miners_solar, "miners_grid": miners_grid, "house_grid": house_grid,
            "house_solar": max(0.0, house - house_grid),
        },
        "solar_known": totals["solar_known"],
        "solar_share_pct": round(100 * miners_solar / miners) if miners else None,
        "self_use_pct": round(100 * (solar - exp) / solar) if solar else None,
        "light": light,
        "best_slice": (
            {"kw": best["kwh"] * 60 / SLICE_MIN, "at": f"{best['slice'] * SLICE_MIN // 60:02d}:{best['slice'] * SLICE_MIN % 60:02d}"}
            if best and best["kwh"] > 0 else None
        ),
        "grid_topup": {"kwh": sum(totals["miners_grid_h"]), "from": window[0], "to": window[1]} if window else None,
        "miners": miner_out,
        "hashes": th_total * 1e12,
        "j_per_th": miners * 3.6e6 / th_total if th_total else None,
        "commands": {"total": len(commands), "start": kinds["start"], "stop": kinds["stop"], "limit": kinds["limit"]},
        "forecast_kwh": state["forecast_at_start_kwh"],
        "forecast_from": _hhmm(state["first_start_at"]),  # the forecast covers the day from then on
        "solar_after_start": (
            solar - state["solar_at_start_kwh"] if state["solar_at_start_kwh"] is not None else None
        ),
        "incomplete": incomplete or totals["incomplete"],
    }


def finish_day(state: dict, incomplete: bool) -> dict:
    """The day that ended, as a kept day not yet reported."""
    close_slice(state["totals"])
    return {"date": state["date"], "report": build_report(state, incomplete=incomplete), "reported": False}


def forecast_after(report: dict) -> float | None:
    """The solar that came in the span the morning's forecast covered: from the first start on
    (a day kept by 0.8.8 has no start mark, so the whole day's solar)."""
    if "solar_after_start" not in report:  # kept by 0.8.8
        return report["totals"]["solar"]
    return report["solar_after_start"]


def forecast_ratio(report: dict) -> float | None:
    """The solar that came against the morning's rough forecast (1.0 = spot on); the forecast
    accuracy sensor and the report's line both use it."""
    # TODO(owner, 2026-10-15): when the forecast service or its settings change (the forecast
    # comparison, docs/plans/2026-10-15-001-...), the sensor's history mixes days before and
    # after. Decide then: note the change date (knowledge base), or reset nothing and read the
    # history with it.
    forecast = report.get("forecast_kwh")
    after = forecast_after(report)
    if not forecast or after is None or not report.get("solar_known"):
        return None
    return after / forecast


# --- the words ------------------------------------------------------------------


def sci(x: float) -> str:
    """6.81e18 → '6.8 × 10¹⁸'."""
    if x <= 0:
        return "0"
    exp = int(math.floor(math.log10(x)))
    sup = str(exp).translate(str.maketrans("-0123456789", "⁻⁰¹²³⁴⁵⁶⁷⁸⁹"))
    return f"{x / 10 ** exp:.1f} × 10{sup}"


def start_burst(starts: list[str], within_min: int = 60, least: int = 3) -> list[str]:
    """The largest run of starts ("HH:MM") inside one window, when it has at least `least`."""
    mins = [int(s[:2]) * 60 + int(s[3:]) for s in starts]
    best: list[str] = []
    for i, a in enumerate(mins):
        run = [starts[j] for j in range(i, len(mins)) if mins[j] - a <= within_min]
        if len(run) > len(best):
            best = run
    return best if len(best) >= least else []


def format_text(d: dict, test: bool = False) -> str:
    """The day report's Telegram text. A reading that is missing leaves its line out."""
    t = d["totals"]
    lines = [TEST_LINE, ""] if test else []
    lines.append(f"📊 Farm day: {d['day_label']}")
    lines.append("")
    if d.get("solar_known", True):
        extra = []
        if d["light"]:
            extra.append(f"{d['light'][0]}–{d['light'][1]}")
        if d["best_slice"]:
            extra.append(f"best 15 min {d['best_slice']['kw']:.1f} kW at {d['best_slice']['at']}")
        lines.append(f"☀️ Solar: {t['solar']:.1f} kWh" + (f" ({', '.join(extra)})" if extra else ""))
    if t["miners"]:
        lines.append(
            f"⛏️ Miners: {t['miners']:.1f} kWh, {d['solar_share_pct']}% from the sun "
            f"({t['miners_solar']:.1f} solar + {t['miners_grid']:.1f} grid)"
        )
    else:
        lines.append("⛏️ Miners: no mining today")
    lines.append(f"🏠 House & other: {t['house']:.1f} kWh, the rest of the balance ({t['house_grid']:.1f} from the grid)")
    lines.append(f"🔌 Grid bought: {t['import']:.1f} kWh · exported: {t['export']:.1f} kWh")
    if d["self_use_pct"] is not None and d.get("solar_known", True):
        lines.append(f"✅ {d['self_use_pct']}% of the solar energy was used on site")
    ratio = forecast_ratio({**d, "solar_known": d.get("solar_known", True)})
    if ratio is not None:
        since = f" from {d['forecast_from']} on" if d.get("forecast_from") else ""
        lines.append(
            f"🔮 Forecast this morning (rough): about {d['forecast_kwh']:.1f} kWh{since}; "
            f"the panels gave {forecast_after(d):.1f} kWh ({ratio:.0%})"
        )
    mining = [m for m in d["miners"] if m["kwh"] > 0.05]
    if mining:
        lines += ["", "Miners"]
        for m in mining:
            parts = [f"{m['kwh']:.1f} kWh", f"{m['hours']:.1f} h", f"avg {m['avg_th_s']:.1f} TH/s"]
            if m["j_per_th"]:
                parts.append(f"{m['j_per_th']:.1f} J/TH")
            lines.append(f"• {m['name']}: " + " · ".join(parts))
    lines.append("")
    c = d["commands"]
    facts = []
    if d["hashes"]:
        facts.append(f"{sci(d['hashes'])} hashes")
    if c["total"]:
        facts.append(f"{c['total']} commands ({c['start']} starts, {c['stop']} stops, {c['limit']} limit changes)")
    if facts:
        lines.append("🔢 " + " · ".join(facts))
    if d["grid_topup"]:
        g = d["grid_topup"]
        lines.append(f"☁️ Grid topped up the miners with {g['kwh']:.1f} kWh, most between {g['from']} and {g['to']}")
    for m in d["miners"]:
        burst = start_burst(m["starts"])
        if burst:
            lines.append(f"⚠️ {m['name']} started {len(burst)}× between {burst[0]} and {burst[-1]} (start/stop flip-flop)")
    if d.get("incomplete"):
        lines.append("⚠️ Home Assistant wasn't running all day: the figures are incomplete")
    return "\n".join(lines).rstrip() + "\n"
