from eveprofile import EveProfile, sales_tax_rate, broker_fee_rate
from eve_api import industry_cost, hub_prices, sell_min
from eve_constants import HUBS


def evaluate_build(
    product_id: int,
    profile: EveProfile,
    buy_hub: str = "jita",
    build_hub: str = "jita",
    sell_hub: str = "jita",
    runs: int = 1,
) -> dict:
    buy_region = HUBS[buy_hub]["region"]
    sell_region = HUBS[sell_hub]["region"]

    ind = industry_cost(product_id, runs=runs, me=profile.me, te=profile.te)
    materials = ind["materials"]
    job_cost = float(ind["total_job_cost"])
    output_units = int(ind["units"])

    mat_ids = [int(tid) for tid in materials]
    buy_prices = hub_prices(mat_ids, region_id=buy_region)
    sell_prices = hub_prices([product_id], region_id=sell_region)

    material_cost = sum(
        int(m["quantity"]) * sell_min(buy_prices, int(tid))
        for tid, m in materials.items()
    )
    sell_revenue = output_units * sell_min(sell_prices, product_id)

    sales_tax = sell_revenue * sales_tax_rate(profile.accounting)
    broker_fee = sell_revenue * broker_fee_rate(profile.broker_relations)
    profit = sell_revenue - sales_tax - broker_fee - material_cost - job_cost
    total_cost = material_cost + job_cost

    return {
        "product_id": product_id,
        "buy_hub": buy_hub,
        "build_hub": build_hub,
        "sell_hub": sell_hub,
        "runs": runs,
        "profile": profile.label,
        "profit": round(profit, 2),
        "margin": round(profit / total_cost, 4) if total_cost else None,
        "breakdown": {
            "sell_revenue": round(sell_revenue, 2),
            "material_cost": round(material_cost, 2),
            "job_cost": round(job_cost, 2),
            "sales_tax": round(sales_tax, 2),
            "broker_fee": round(broker_fee, 2),
        },
    }
