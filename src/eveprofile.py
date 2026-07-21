from dataclasses import dataclass


@dataclass
class EveProfile:
    """How you build — affects cost, fees, time. Stage 1: hand-entered.
    Stage 2: me/te from the blueprints endpoint, the skills from the skills endpoint."""

    me: int = 10  # blueprint material efficiency (0-10)
    te: int = 20  # blueprint time efficiency (0-20)
    accounting: int = 0  # reduces sales tax
    broker_relations: int = 0  # reduces broker fee
    industry: int = 0  # build time (used in Stage 2)
    advanced_industry: int = 0
    character_id: int | None = None
    label: str = "manual"


def sales_tax_rate(accounting: int) -> float:
    # Base 7.5%; Accounting reduces 11%/level. Accounting V -> 3.375%.
    return 0.075 * (1 - 0.11 * accounting)


def broker_fee_rate(broker_relations: int) -> float:
    # Approximate: NPC base ~3%, Broker Relations ~-0.3%/level (standings ignored).
    # Verify the exact base + your standings in-game; this is a config knob.
    return max(0.0, 0.03 - 0.003 * broker_relations)
