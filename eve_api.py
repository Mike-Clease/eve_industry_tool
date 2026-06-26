# eve_api.py
import httpx
from eve_constants import HUBS, ITEMS

ESI = "https://esi.evetech.net"
ESI_HEADERS = {
    "X-Compatibility-Date": "2026-06-26",          # pin behaviour to a known date
    "User-Agent": "forge-analyst/0.1 (clease.m@gmail.com)",  # put real contact info here
}

def price_history(type_id: int, region_id: int = 10000002, days: int = 90) -> list[dict]:
    """Daily market history for one item in one region (ESI returns ~500 days)."""
    url = f"{ESI}/markets/{region_id}/history/"
    r = httpx.get(url, params={"type_id": type_id}, headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()[-days:]
    # each row: {date, average, highest, lowest, order_count, volume}


if __name__ == "__main__":
    print(price_history(type_id=ITEMS["rifter"], region_id=HUBS["jita"]["region"], days=1))