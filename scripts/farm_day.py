#!/usr/bin/env python3
"""One farm day as a report: the totals, an infographic card (PNG) and a Telegram text.

Run from the repo root:

  python3 scripts/farm_day.py build 2026-10-12 [--test] [--out DIR]
      Download the day from the farm's Home Assistant (recorder history), work out the energy
      and the miners' day, and write farm-day-<date>.json / .txt / .png into DIR
      (default config/farm-history/reports/). --test marks the card and the text as a test.

  python3 scripts/farm_day.py send 2026-10-12 [--test] [--out DIR]
      Build, then send the card (photo with a short caption) and the long text to the Telegram
      chat in secrets/telegram.env (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID). Neither is printed.

Needs secrets/ha-agent.env (HA_URL, HA_TOKEN) and secrets/farm-day.json, the farm's entity map
(see .claude/skills/farm-day-report/farm-day.example.json). The card is rendered by headless
Google Chrome. Every figure comes from energy counters, not from power readings: on the
reference farm the inverters' power readings have gaps, the counters don't.

This is a development tool and the prototype of the integration's day report; nothing in the
integration imports it.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import datetime as dt
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
CARD_TEMPLATE = Path(__file__).with_name("farm_day_card.html")
DEFAULT_OUT = ROOT / "config" / "farm-history" / "reports"
BIN_MIN = 15
MAX_BIN_KWH = 5.0  # a counter step larger than this in 15 minutes is a glitch, not energy
MINING_TH_S = 1.0  # hashrate above this counts as mining
LIGHT_W = 50  # inverter output above this counts as production
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
TEST_LINE = "🧪 TEST MESSAGE: daily report preview, please ignore"

Points = list[tuple[dt.datetime, float]]


# ---------------------------------------------------------------- reading the history


def points(rows: list[dict], tz: ZoneInfo) -> Points:
    """(local time, value) for every numeric state; unknown and unavailable are dropped."""
    out: Points = []
    for row in rows:
        try:
            value = float(row["state"])
        except (TypeError, ValueError):
            continue
        out.append((dt.datetime.fromisoformat(row["last_changed"]).astimezone(tz), value))
    return out


def value_at(pts: Points, t: dt.datetime) -> float | None:
    """The counter's last value at or before t (the first one when none is earlier)."""
    found = None
    for when, value in pts:
        if when <= t:
            found = value
        else:
            break
    if found is None and pts:
        found = pts[0][1]
    return found


def counter_bins(pts: Points, start: dt.datetime, n: int, daily_reset: bool = False) -> list[float]:
    """Energy (kWh) per BIN_MIN slice from a cumulative counter.

    A daily counter that falls back counts from zero (its midnight reset); a total counter that
    falls back, or jumps more than MAX_BIN_KWH, is a glitch and the slice gets nothing.
    """
    out = []
    for k in range(n):
        a = value_at(pts, start + dt.timedelta(minutes=k * BIN_MIN))
        b = value_at(pts, start + dt.timedelta(minutes=(k + 1) * BIN_MIN))
        if a is None or b is None:
            out.append(0.0)
            continue
        delta = b - a
        if delta < 0:
            delta = b if daily_reset else 0.0
        out.append(delta if 0 <= delta <= MAX_BIN_KWH else 0.0)
    return out


def time_weighted(pts: Points, start: dt.datetime, end: dt.datetime) -> tuple[float, float]:
    """(integral of value × seconds, seconds above MINING_TH_S) between start and end."""
    total = 0.0
    above = 0.0
    for i, (when, value) in enumerate(pts):
        nxt = pts[i + 1][0] if i + 1 < len(pts) else end
        seconds = (min(nxt, end) - max(when, start)).total_seconds()
        if seconds <= 0:
            continue
        total += value * seconds
        if value > MINING_TH_S:
            above += seconds
    return total, above


def ok_commands(rows: list[dict], start: dt.datetime, tz: ZoneInfo) -> list[tuple[dt.datetime, str]]:
    """The finished commands of the day from the integration's last-action sensor.

    The state carried over from the day before is not a command of this day, and the same
    finished command shown again after the sensor was unavailable is not a new one.
    """
    out: list[tuple[dt.datetime, str]] = []
    last_ok = None
    for row in rows:
        state = row["state"]
        when = dt.datetime.fromisoformat(row["last_changed"]).astimezone(tz)
        if not state.endswith("(ok)"):
            continue
        if when > start and state != last_ok:
            out.append((when, state))
        last_ok = state
    return out


def command_kind(text: str) -> str:
    return "stop" if "stop" in text else "start" if "start" in text else "limit"


# ---------------------------------------------------------------- the day


@dataclass
class Miner:
    name: str
    kwh: float
    hours: float
    avg_th_s: float
    hashes: float
    j_per_th: float | None
    first: str | None
    last: str | None
    starts: list[str]
    watts: list[int]  # average draw per BIN_MIN slice


def compute_day(history: dict[str, list[dict]], farm: dict, day: dt.date, tz: ZoneInfo) -> dict:
    """Everything the card and the text show, from the recorder history of one local day."""
    start = dt.datetime.combine(day, dt.time(), tz)
    end = start + dt.timedelta(days=1)
    n = int((end - start).total_seconds() // (BIN_MIN * 60))
    pts = {e: points(rows, tz) for e, rows in history.items()}
    get = lambda e: pts.get(e, [])  # noqa: E731

    solar = [sum(s) for s in zip(*[counter_bins(get(e), start, n, True) for e in farm["solar_energy_today"]])]
    imp = counter_bins(get(farm["grid_import_total"]), start, n)
    exp = counter_bins(get(farm["grid_export_total"]), start, n) if farm.get("grid_export_total") else [0.0] * n
    miner_bins = [counter_bins(get(m["energy_total"]), start, n) for m in farm["miners"]]
    miners = [sum(s) for s in zip(*miner_bins)] if miner_bins else [0.0] * n

    m_solar = [min(m, max(0.0, s - e)) for m, s, e in zip(miners, solar, exp)]
    house = [s + i - e - m for s, i, e, m in zip(solar, imp, exp, miners)]

    # first and last production: the inverters' summed power when recorded, else the energy slices
    power = [get(e) for e in farm.get("solar_power", [])]
    changes = sorted({t for p in power for t, _ in p if start <= t < end})
    lit = [t for t in changes if sum(value_at(p, t) or 0.0 for p in power) > LIGHT_W]
    if lit:
        light = (lit[0], lit[-1])
    else:
        on = [k for k, s in enumerate(solar) if s > 0]
        light = (start + dt.timedelta(minutes=on[0] * BIN_MIN), start + dt.timedelta(minutes=(on[-1] + 1) * BIN_MIN)) if on else None

    commands = ok_commands(history.get(farm.get("last_action", ""), []), start, tz)
    kinds = Counter(command_kind(c) for _, c in commands)

    miner_out = []
    for spec, bins in zip(farm["miners"], miner_bins):
        hp = get(spec["hashrate"])
        th_seconds, mining_s = time_weighted(hp, start, end)
        on_times = [t for t, v in hp if v > MINING_TH_S and start <= t < end]
        kwh = sum(bins)
        th = th_seconds  # TH/s × s = TH
        miner_out.append(Miner(
            name=spec["name"],
            kwh=kwh,
            hours=mining_s / 3600,
            avg_th_s=th_seconds / mining_s if mining_s else 0.0,
            hashes=th * 1e12,
            j_per_th=kwh * 3.6e6 / th if th else None,
            first=on_times[0].strftime("%H:%M") if on_times else None,
            last=on_times[-1].strftime("%H:%M") if on_times else None,
            starts=[t.strftime("%H:%M") for t, c in commands if c.startswith(spec["name"] + ":") and command_kind(c) == "start"],
            watts=[round(b * 60 / BIN_MIN * 1000) for b in bins],
        ))

    per_hour = 60 // BIN_MIN
    hour = lambda xs: [sum(xs[h * per_hour:(h + 1) * per_hour]) for h in range(24)]  # noqa: E731
    h_solar, h_miners, h_imp, h_exp = hour(solar), hour(miners), hour(imp), hour(exp)
    h_mgrid = [m - s for m, s in zip(hour(miners), hour(m_solar))]
    h_house = [max(0.0, s + i - e - m) for s, i, e, m in zip(h_solar, h_imp, h_exp, h_miners)]

    totals = {
        "solar": sum(solar), "miners": sum(miners), "import": sum(imp), "export": sum(exp),
        "house": sum(house), "miners_solar": sum(m_solar),
    }
    totals["miners_grid"] = totals["miners"] - totals["miners_solar"]
    totals["house_grid"] = max(0.0, totals["import"] - totals["miners_grid"])
    totals["house_solar"] = max(0.0, totals["house"] - totals["house_grid"])
    best = max(range(n), key=lambda k: solar[k]) if any(solar) else None
    dark = [h for h in range(24) if h_solar[h] == 0]
    window = _grid_window(h_mgrid)
    hashes = sum(m.hashes for m in miner_out)
    th_total = hashes / 1e12

    return {
        "farm": farm.get("name", "Solar mining farm"),
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "day_label": f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}",
        "day_short": f"{day.strftime('%a')} {day.day} {day.strftime('%b %Y')}",
        "totals": totals,
        "solar_share_pct": round(100 * totals["miners_solar"] / totals["miners"]) if totals["miners"] else None,
        "self_use_pct": round(100 * (totals["solar"] - totals["export"]) / totals["solar"]) if totals["solar"] else None,
        "light": [light[0].strftime("%H:%M"), light[1].strftime("%H:%M")] if light else None,
        "light_h": [light[0].hour + light[0].minute / 60, light[1].hour + light[1].minute / 60] if light else None,
        "best_slice": {"kw": solar[best] * 60 / BIN_MIN, "at": (start + dt.timedelta(minutes=best * BIN_MIN)).strftime("%H:%M")} if best is not None else None,
        "night_house_kwh_h": sum(h_house[h] for h in dark) / len(dark) if dark else None,
        "grid_topup": {"kwh": sum(h_mgrid), "from": window[0], "to": window[1]} if window else None,
        "hourly": {"solar": h_solar, "miners": h_miners, "house": h_house, "miners_grid": h_mgrid},
        "miners": [m.__dict__ for m in miner_out],
        "hashes": hashes,
        "j_per_th": totals["miners"] * 3.6e6 / th_total if th_total else None,
        "commands": {"total": len(commands), "start": kinds["start"], "stop": kinds["stop"], "limit": kinds["limit"]},
        "bin_minutes": BIN_MIN,
    }


def _grid_window(h_mgrid: list[float], width: int = 3) -> tuple[str, str] | None:
    """The `width` consecutive hours in which the grid gave the miners the most, when it gave any."""
    if sum(h_mgrid) < 0.05:
        return None
    best = max(range(24 - width + 1), key=lambda h: sum(h_mgrid[h:h + width]))
    return f"{best:02d}:00", f"{best + width:02d}:00"


# ---------------------------------------------------------------- words


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


def headline(d: dict) -> str:
    if not d["totals"]["miners"]:
        return "No mining today"
    return f"{d['solar_share_pct']}% of the miners' power came from the sun"


def format_text(d: dict, test: bool = False) -> str:
    """The long Telegram text. A reading that is missing leaves its part out."""
    t = d["totals"]
    lines = [TEST_LINE, ""] if test else []
    lines.append(f"📊 Farm day: {d['day_label']}")
    lines.append("")
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
    lines.append(f"🏠 House & other: {t['house']:.1f} kWh ({t['house_grid']:.1f} from the grid)")
    lines.append(f"🔌 Grid bought: {t['import']:.1f} kWh · exported: {t['export']:.1f} kWh")
    if d["self_use_pct"] is not None:
        lines.append(f"✅ {d['self_use_pct']}% of the solar energy was used on site")
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
    return "\n".join(lines).rstrip() + "\n"


def format_caption(d: dict, test: bool = False) -> str:
    """The short caption under the card (Telegram allows 1024 characters)."""
    t = d["totals"]
    lines = [TEST_LINE, ""] if test else []
    lines.append(f"📊 Farm day, {d['day_short']}")
    miners = f"⛏️ Miners {t['miners']:.1f} kWh ({d['solar_share_pct']}% from the sun)" if t["miners"] else "⛏️ No mining"
    lines.append(f"☀️ Solar {t['solar']:.1f} kWh · {miners}")
    lines.append(f"🏠 House {t['house']:.1f} kWh · 🔌 Grid {t['import']:.1f} kWh in, {t['export']:.1f} out")
    return "\n".join(lines)


# ---------------------------------------------------------------- input and output


def load_env(path: Path) -> dict[str, str]:
    env = {}
    for line in path.read_text().splitlines():
        line = line.strip().removeprefix("export ")
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def ha_get(env: dict, path: str) -> object:
    req = urllib.request.Request(env["HA_URL"].rstrip("/") + path, headers={"Authorization": "Bearer " + env["HA_TOKEN"]})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def entities(farm: dict) -> list[str]:
    ids = list(farm["solar_energy_today"]) + list(farm.get("solar_power", []))
    ids += [farm["grid_import_total"]] + ([farm["grid_export_total"]] if farm.get("grid_export_total") else [])
    for m in farm["miners"]:
        ids += [m["energy_total"], m["hashrate"]]
    if farm.get("last_action"):
        ids.append(farm["last_action"])
    return ids


def fetch(day: dt.date, farm: dict) -> tuple[dict[str, list[dict]], ZoneInfo]:
    env = load_env(ROOT / "secrets" / "ha-agent.env")
    tz = ZoneInfo(ha_get(env, "/api/config")["time_zone"])
    start = dt.datetime.combine(day, dt.time(), tz).astimezone(dt.UTC)
    end = start + dt.timedelta(days=1)
    q = urllib.parse.urlencode({
        "end_time": end.isoformat(), "filter_entity_id": ",".join(entities(farm)),
        "minimal_response": "", "no_attributes": "",
    })
    rows = ha_get(env, f"/api/history/period/{urllib.parse.quote(start.isoformat())}?{q}")
    history = {series[0]["entity_id"]: series for series in rows if series}
    for series in history.values():  # minimal_response keeps entity_id on the first row only
        for row in series:
            row.setdefault("entity_id", series[0]["entity_id"])
    return history, tz


def render_card(d: dict, test: bool, png: Path) -> None:
    chrome = CHROME if Path(CHROME).exists() else shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        sys.exit("Google Chrome is needed to render the card.")
    html = CARD_TEMPLATE.read_text().replace("/*DAY*/null", json.dumps({**d, "test": test, "headline": headline(d), "hashes_sci": sci(d["hashes"])}))
    with tempfile.TemporaryDirectory() as tmp:
        page = Path(tmp) / "card.html"
        page.write_text(html)
        subprocess.run(
            [chrome, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-device-scale-factor=2",
             "--window-size=540,675", "--virtual-time-budget=5000", f"--screenshot={png}", page.as_uri()],
            check=True, capture_output=True,
        )


def telegram(method: str, fields: dict[str, str], files: dict[str, Path] | None = None) -> None:
    env = load_env(ROOT / "secrets" / "telegram.env")
    boundary = uuid.uuid4().hex
    body = b""
    for k, v in fields.items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    for k, p in (files or {}).items():
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{p.name}"\r\nContent-Type: image/png\r\n\r\n'.encode()
        body += p.read_bytes() + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        TELEGRAM_API.format(token=env["TELEGRAM_BOT_TOKEN"], method=method), data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(f"Telegram {method}: HTTP {resp.status}")
    except urllib.error.HTTPError as err:  # the URL holds the token: report the status and Telegram's words only
        sys.exit(f"Telegram {method} failed: HTTP {err.code} {err.read()[:200].decode(errors='replace')}")
    except urllib.error.URLError as err:
        sys.exit(f"Telegram {method} failed: {type(err.reason).__name__}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["build", "send"])
    ap.add_argument("date", help="local day, YYYY-MM-DD (default: yesterday)", nargs="?")
    ap.add_argument("--test", action="store_true", help="mark the card and the text as a test")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--farm", type=Path, default=ROOT / "secrets" / "farm-day.json")
    args = ap.parse_args()

    day = dt.date.fromisoformat(args.date) if args.date else dt.date.today() - dt.timedelta(days=1)
    farm = json.loads(args.farm.read_text())
    history, tz = fetch(day, farm)
    d = compute_day(history, farm, day, tz)
    args.out.mkdir(parents=True, exist_ok=True)
    stem = args.out / f"farm-day-{day.isoformat()}"
    Path(f"{stem}.json").write_text(json.dumps(d, indent=1))
    text = format_text(d, args.test)
    Path(f"{stem}.txt").write_text(text)
    png = Path(f"{stem}.png")
    render_card(d, args.test, png)
    print(text)
    print(f"Card: {png}\nText: {stem}.txt\nData: {stem}.json")

    if args.action == "send":
        telegram("sendPhoto", {"chat_id": load_env(ROOT / "secrets" / "telegram.env")["TELEGRAM_CHAT_ID"], "caption": format_caption(d, args.test)}, {"photo": png})
        telegram("sendMessage", {"chat_id": load_env(ROOT / "secrets" / "telegram.env")["TELEGRAM_CHAT_ID"], "text": text, "disable_web_page_preview": "true"})


if __name__ == "__main__":
    main()
