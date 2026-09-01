from eveprofile import EveProfile
from eve_constants import HUBS


me_profile = EveProfile(me=10, accounting=5, broker_relations=4)


def evaluate_build(buy_hub, sell_hub):
    buy_region = HUBS[buy_hub]["region"]
    sell_region = HUBS[sell_hub]["region"]

    return buy_region, sell_region
