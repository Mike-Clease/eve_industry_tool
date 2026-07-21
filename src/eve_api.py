# eve_api.py
import httpx
import csv
import functools

ESI = "https://esi.evetech.net"
ESI_HEADERS = {
    "X-Compatibility-Date": "2026-06-26",  # pin behaviour to a known date
    "User-Agent": "forge-analyst/0.1 (clease.m@gmail.com)",  # put real contact info here
}
FUZZWORKS = "https://www.fuzzwork.co.uk/dump/latest/csv/mapSolarSystems.csv"
FUZZ = "https://market.fuzzwork.co.uk/aggregates/"
EVEREF = "https://api.everef.net/v1/industry/cost"


def resolve_type(name: str) -> int:
    """Returns a type_id for a supplied item name."""
    r = httpx.post(f"{ESI}/universe/ids/", json=[name], headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    hits = r.json().get("inventory_types", [])
    if not hits:
        raise ValueError(f"no type found for {name!r}")
    return hits[0]["id"]


def resolve_system(name: str) -> int:
    """Returns a system_id for a supplied item name."""
    r = httpx.post(f"{ESI}/universe/ids/", json=[name], headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    hits = r.json().get("systems", [])
    if not hits:
        raise ValueError(f"no system found for {name!r}")
    return hits[0]["id"]


def price_history(
    type_id: int, region_id: int = 10000002, days: int = 90
) -> list[dict]:
    """Daily market history for one item in one region (ESI returns ~500 days)."""
    url = f"{ESI}/markets/{region_id}/history/"
    r = httpx.get(url, params={"type_id": type_id}, headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()[-days:]
    # each row: {date, average, highest, lowest, order_count, volume}


@functools.lru_cache(maxsize=1)
def _system_to_region() -> dict[int, int]:
    """systemID -> regionID from the SDE — one download (~8k rows), then fully offline."""
    text = httpx.get(FUZZWORKS, timeout=60).content.decode("utf-8-sig")
    return {
        int(r["solarSystemID"]): int(r["regionID"])
        for r in csv.DictReader(text.splitlines())
    }


def region_of_system(system_id: int) -> int:  # drop-in, no HTTP per call
    return _system_to_region()[system_id]


def hub_prices(type_ids: list[int], region_id: int = 10000002) -> dict:
    """Returns {'<type_id>': {'buy': {...}, 'sell': {...}}, ...}."""
    ids = ",".join(str(t) for t in type_ids)
    r = httpx.get(FUZZ, params={"region": region_id, "types": ids}, timeout=30)
    r.raise_for_status()
    return r.json()


def sell_min(prices: dict, type_id: int) -> float:
    """Cheapest sell order = what you pay to buy this item instantly."""
    return float(prices[str(type_id)]["sell"]["min"])


def buy_max(prices: dict, type_id: int) -> float:
    """Highest buy order = what you get selling into buy orders instantly."""
    return float(prices[str(type_id)]["buy"]["max"])


def industry_cost(product_id: int, runs: int = 1, me: int = 10, te: int = 20) -> dict:
    """ME-applied materials + total job-install cost for manufacturing product_id."""
    r = httpx.get(
        EVEREF,
        params={
            "product_id": product_id,
            "runs": runs,
            "me": me,
            "te": te,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["manufacturing"][str(product_id)]
    # keys used: materials {type_id: {quantity, cost}}, total_job_cost, units, estimated_item_value


if __name__ == "__main__":
    sys = resolve_system("jita")
    region = region_of_system(sys)
    print(region)
