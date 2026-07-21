from eve_api import price_history, resolve_type
from eveprofile import EveProfile
from evaluate_build import evaluate_build


rifter = resolve_type("Rifter")  # name -> type_id
me_profile = EveProfile(me=10, accounting=5, broker_relations=4)
r = evaluate_build(
    rifter, me_profile, buy_hub="jita", build_hub="korsiki", sell_hub="korsiki"
)
print(r["profit"], r["margin"])
print(r["breakdown"])
print("history rows:", len(price_history(rifter, days=90)))
