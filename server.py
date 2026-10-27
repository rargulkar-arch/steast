"""
Stetson Menu connector
A small MCP server that fetches today's menu (with nutrition) for
The Eatery at Stetson East from Dine On Campus, so Claude and the
Stetson Plate tracker can look it up live.

Dine On Campus has no official public API. This uses the same
undocumented endpoints its website calls, with browser-like requests
(curl_cffi), because plain HTTP clients get a reduced menu. The
endpoints can change without notice: run `python server.py --probe`
to check what's working.
"""
import os, sys, json, datetime as dt
from zoneinfo import ZoneInfo
from cachetools import TTLCache
from curl_cffi import requests as creq
from mcp.server.fastmcp import FastMCP

BOSTON = ZoneInfo("America/New_York")
SITE_SLUGS = [s for s in os.getenv("DOC_SITE_SLUGS", "northeastern,public").split(",") if s]
LOCATION_MATCH = os.getenv("DOC_LOCATION", "stetson east").lower()
HOSTS = [h for h in os.getenv("DOC_HOSTS", "https://apiv4.dineoncampus.com,https://api.dineoncampus.com").split(",") if h]

HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://dineoncampus.com",
    "Referer": "https://dineoncampus.com/",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}
session = creq.Session(impersonate="chrome")
site_cache = TTLCache(maxsize=4, ttl=24 * 3600)
period_cache = TTLCache(maxsize=64, ttl=3600)
menu_cache = TTLCache(maxsize=128, ttl=2 * 3600)


def _get(url, params=None):
    r = session.get(url, params=params, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def _first_ok(urls, params=None):
    """Try candidate URLs in order; return (url, json) for the first that answers."""
    errors = []
    for u in urls:
        try:
            return u, _get(u, params)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{u}: {e}")
    raise RuntimeError("All endpoints failed:\n" + "\n".join(errors))


def _walk(obj):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from _walk(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk(v)


def _date_param(d: dt.date):
    return f"{d.year}-{d.month}-{d.day}"


def resolve_site():
    """Find the site id and the Stetson East location id."""
    if "site" in site_cache:
        return site_cache["site"]
    for host in HOSTS:
        for slug in SITE_SLUGS:
            try:
                _, info = _first_ok([f"{host}/v1/sites/{slug}/info"])
                site_id = (info.get("site") or {}).get("id") or info.get("id")
                if not site_id:
                    continue
                _, locs = _first_ok([
                    f"{host}/v1/locations/status?site_id={site_id}&platform=0",
                    f"{host}/v1/locations/buildings?site_id={site_id}&platform=0",
                ])
                for d in _walk(locs):
                    name = str(d.get("name", "")).lower()
                    if d.get("id") and LOCATION_MATCH in name:
                        found = {"host": host, "site_id": site_id, "location_id": d["id"], "location_name": d.get("name")}
                        site_cache["site"] = found
                        return found
            except Exception:  # noqa: BLE001
                continue
    raise RuntimeError("Couldn't find Stetson East on Dine On Campus. Run `python server.py --probe` for details.")


def get_periods(day: dt.date):
    key = ("p", day.isoformat())
    if key in period_cache:
        return period_cache[key]
    s = resolve_site()
    _, data = _first_ok([
        f"{s['host']}/v1/location/{s['location_id']}/periods?platform=0&date={_date_param(day)}",
        f"{s['host']}/v4/location/{s['location_id']}/periods?date={day.isoformat()}",
    ])
    periods = [{"id": p["id"], "name": p.get("name", "")} for p in (data.get("periods") or []) if isinstance(p, dict) and p.get("id")]
    if not periods:  # fall back to any list of id+name objects
        periods = [{"id": d["id"], "name": d["name"]} for d in _walk(data) if d.get("id") and d.get("name") and "categor" not in json.dumps(d)[:200].lower()]
    period_cache[key] = periods
    return periods


NUTRIENT_KEYS = {
    "calories": ["calories"],
    "protein": ["protein"],
    "carbs": ["total carbohydrates", "total carbohydrate", "carbohydrates", "carbs"],
    "fat": ["total fat", "fat"],
    "fiber": ["dietary fiber", "fiber"],
    "sugar": ["sugar", "total sugars", "sugars"],
    "sodium": ["sodium"],
}


def _num(v):
    try:
        return round(float(str(v).replace("<", "").replace("g", "").replace("mg", "").strip()), 1)
    except Exception:  # noqa: BLE001
        return None


def _parse_item(it):
    out = {"name": it.get("name"), "portion": it.get("portion") or it.get("serving_size"), "calories": _num(it.get("calories"))}
    nutrients = {str(n.get("name", "")).lower(): n.get("value") for n in (it.get("nutrients") or []) if isinstance(n, dict)}
    for key, names in NUTRIENT_KEYS.items():
        if out.get(key) is None:
            for nm in names:
                for k, v in nutrients.items():
                    if k.startswith(nm):
                        out[key] = _num(v)
                        break
                if out.get(key) is not None:
                    break
        out.setdefault(key, None)
    return out


def _parse_menu(data):
    stations = []
    for d in _walk(data):
        cats = d.get("categories")
        if isinstance(cats, list) and cats and isinstance(cats[0], dict) and "items" in cats[0]:
            for c in cats:
                items = [_parse_item(i) for i in (c.get("items") or []) if isinstance(i, dict) and i.get("name")]
                if items:
                    stations.append({"station": c.get("name"), "items": items})
            break
    return stations


def fetch_menu(day: dt.date, period: dict):
    key = (day.isoformat(), period["id"])
    if key in menu_cache:
        return menu_cache[key]
    s = resolve_site()
    _, data = _first_ok([
        f"{s['host']}/v1/location/{s['location_id']}/periods/{period['id']}?platform=0&date={_date_param(day)}",
        f"{s['host']}/v4/location/{s['location_id']}/menu?date={day.isoformat()}&period={period['id']}",
    ])
    stations = _parse_menu(data)
    if stations:
        menu_cache[key] = stations
    return stations


def _pick_period(periods, meal):
    if not periods:
        return None
    if meal:
        for p in periods:
            if meal.lower() in p["name"].lower():
                return p
    h = dt.datetime.now(BOSTON).hour
    guess = "breakfast" if h < 11 else "lunch" if h < 16 else "dinner"
    for p in periods:
        if guess in p["name"].lower():
            return p
    return periods[0]


mcp = FastMCP("Stetson Menu", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), stateless_http=True)


@mcp.tool()
def get_stetson_menu(date: str | None = None, meal: str | None = None) -> dict:
    """Get the menu for The Eatery at Stetson East (Northeastern University) with
    calories, protein, carbs, fat, fiber, sugar and sodium per portion, grouped by station.
    date: YYYY-MM-DD, defaults to today in Boston. meal: e.g. breakfast, lunch, dinner,
    everyday; defaults to the current meal period."""
    day = dt.date.fromisoformat(date) if date else dt.datetime.now(BOSTON).date()
    try:
        periods = get_periods(day)
        period = _pick_period(periods, meal)
        if not period:
            return {"status": "no_menu", "date": day.isoformat(), "reason": "Stetson East has no meal periods posted for this date."}
        stations = fetch_menu(day, period)
        if not stations:  # stale period id: refresh once
            period_cache.pop(("p", day.isoformat()), None)
            period = _pick_period(get_periods(day), meal) or period
            stations = fetch_menu(day, period)
        return {"status": "ok", "location": "The Eatery at Stetson East", "date": day.isoformat(),
                "meal": period["name"], "available_meals": [p["name"] for p in periods], "stations": stations}
    except Exception as e:  # noqa: BLE001
        return {"status": "unavailable", "date": day.isoformat(), "reason": str(e)[:500]}


@mcp.tool()
def list_stetson_meals(date: str | None = None) -> dict:
    """List the meal periods Stetson East has posted for a date (YYYY-MM-DD, default today)."""
    day = dt.date.fromisoformat(date) if date else dt.datetime.now(BOSTON).date()
    try:
        return {"status": "ok", "date": day.isoformat(), "meals": [p["name"] for p in get_periods(day)]}
    except Exception as e:  # noqa: BLE001
        return {"status": "unavailable", "date": day.isoformat(), "reason": str(e)[:500]}


def probe():
    print("Resolving Stetson East…")
    try:
        print(json.dumps(resolve_site(), indent=2))
        today = dt.datetime.now(BOSTON).date()
        print("Meals today:", [p["name"] for p in get_periods(today)])
        res = get_stetson_menu()
        print("Status:", res.get("status"), "| meal:", res.get("meal"))
        for st in res.get("stations", [])[:3]:
            print(" ", st["station"], "→", [i["name"] for i in st["items"][:3]])
        if res.get("status") != "ok":
            print(res.get("reason"))
    except Exception as e:  # noqa: BLE001
        print("Probe failed:", e)


if __name__ == "__main__":
    if "--probe" in sys.argv:
        probe()
    else:
        mcp.run(transport="streamable-http")
