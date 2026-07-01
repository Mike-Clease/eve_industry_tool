# eve_constants.py
# Trade hubs: region id, and the hub station id (for hub-specific pricing)
HUBS = {
    "jita":     {"region": 10000002, "station": 60003760},  # The Forge / Jita IV-4
    "amarr":    {"region": 10000043, "station": 60008494},  # Domain
    "dodixie":  {"region": 10000032, "station": 60011866},  # Sinq Laison
    "rens":     {"region": 10000030, "station": 60004588},  # Heimatar
    "hek":      {"region": 10000042, "station": 60005686},  # Metropolis
}

# A couple of known type ids to test with:
ITEMS = {
   "tritanium": 34, # Tritanium (a mineral, always liquid)
   "rifter": 587 # Rifter (a cheap T1 frigate — good build test case)
}