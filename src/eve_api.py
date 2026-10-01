# eve_api.py
import httpx

ESI = "https://esi.evetech.net"
ESI_HEADERS = {
    "X-Compatibility-Date": "2026-06-26",  # pin behaviour to a known date
    "User-Agent": "forge-analyst/0.1 (clease.m@gmail.com)",  # put real contact info here
}
FUZZ = "https://market.fuzzwork.co.uk/aggregates/"
EVEREF = "https://api.everef.net/v1/industry/cost"


def get_prices():
    url = f"{ESI}/markets/prices"
    r = httpx.get(url, headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    # save this as a csv or parquet???
