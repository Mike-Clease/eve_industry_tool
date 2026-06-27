# Building Forge Analyst — Stage 1 (step-by-step)

A hands-on build of Stage 1: search for an item, get a full build-cost / profit breakdown at a hub, rank a watchlist, approve the shortlist, and chart 90-day prices — all running through MCP servers behind a LangGraph graph. This is the Stage 1 of `ROADMAP.md`; Stages 2 (character data) and 3 (demand predictor) build on it.

You build it yourself, part by part. Each part ends with a **checkpoint** — run it, confirm the output, then move on. Later parts assume the earlier ones run.

**Three things baked in from the start** (the seams that make Stages 2–3 cheap):
1. *Skills-parametric* — the calculator takes a `Profile` argument; Stage 2 just swaps where that profile comes from (you type it → ESI hands it over).
2. *Framework-first* — even the single-item lookup runs through MCP + LangGraph, so the scaffolding is in before the logic gets hard.
3. *Ingest-early* — Part 7 switches on a background logger that accumulates the history Stage 3's model will need. Start it now; it runs while you build.

**Rough time:** a focused weekend. **You need:** Python 3.11+ and an EVE character whose Accounting / Broker Relations / blueprint ME you know (entered by hand in Stage 1). No Anthropic key and no EVE SSO needed yet — the money maths is deterministic, and the optional LLM layer (which would need a key) is a Stage 3 stretch.

The order is deliberate: **understand the data and the maths first as plain functions (Part 1)**, then wrap them as MCP servers (Parts 2–3), then put the graph on top (Parts 4–6). If the maths is wrong, no amount of orchestration saves you.

---

## Part 0 — Environment

```bash
mkdir forge-analyst && cd forge-analyst
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install httpx duckdb plotly langgraph langgraph-checkpoint-sqlite \
            langchain-mcp-adapters "mcp[cli]"
# langchain[anthropic] is only needed later, if you add the optional LLM layer.
```

Create `eve_constants.py` — the IDs you'll reference everywhere:

```python
# eve_constants.py
# Trade hubs: region id + hub station id (for hub-specific pricing)
HUBS = {
    "jita":    {"region": 10000002, "station": 60003760},  # The Forge / Jita IV-4
    "amarr":   {"region": 10000043, "station": 60008494},  # Domain
    "dodixie": {"region": 10000032, "station": 60011866},  # Sinq Laison
    "rens":    {"region": 10000030, "station": 60004588},  # Heimatar
    "hek":     {"region": 10000042, "station": 60005686},  # Metropolis
}
# Test items: 34 = Tritanium (always liquid), 587 = Rifter (cheap T1 build).
```

---

## Part 1 — Talk to the APIs as plain functions

No MCP, no LangGraph yet. The goal: understand exactly what each service gives you and compute a profit you can verify by hand. Create `eve_api.py`.

### 1a. Name → type_id (the front door)

"Search for an item" starts here. ESI resolves an exact name to its type_id.

```python
# eve_api.py
import httpx

ESI = "https://esi.evetech.net"
ESI_HEADERS = {
    "X-Compatibility-Date": "2026-06-26",                 # pin behaviour to a known date
    "User-Agent": "forge-analyst/0.1 (you@example.com)",  # put real contact info here
}

def resolve_type(name: str) -> int:
    """Exact item name -> type_id (ESI /universe/ids/)."""
    r = httpx.post(f"{ESI}/universe/ids/", json=[name], headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    hits = r.json().get("inventory_types", [])
    if not hits:
        raise ValueError(f"no type found for {name!r}")
    return hits[0]["id"]
    # For fuzzy/partial matches use ESI /search or the Fuzzwork type dump instead.
```

### 1b. Market history (ESI) — for your charts

ESI is migrating from versioned URLs (`/latest/`, `/v4/`) to the `X-Compatibility-Date` header. Use the header; always send a `User-Agent`, or you risk being throttled or banned.

```python
def price_history(type_id: int, region_id: int = 10000002, days: int = 90) -> list[dict]:
    """Daily market history for one item in one region (ESI returns ~500 days)."""
    url = f"{ESI}/markets/{region_id}/history/"
    r = httpx.get(url, params={"type_id": type_id}, headers=ESI_HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()[-days:]
    # each row: {date, average, highest, lowest, order_count, volume}
```

### 1c. Current hub prices (Fuzzwork) — what you pay / receive now

Fuzzwork pre-aggregates the order book, so one call gets min/max/median for a list of items.

```python
FUZZ = "https://market.fuzzwork.co.uk/aggregates/"

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
```

> **Why two prices matter:** buying materials at `sell_min` is instant but dearer; selling output at `sell_min` (undercutting the cheapest seller) means listing and waiting. Stage 1 assumes: buy materials at `sell_min`, list output near `sell_min`. Make the assumption explicit so the number is honest.

### 1d. The industry calculation (EVE Ref) — recipe, ME, job cost

Blueprint requirements aren't in ESI; they're in the SDE. Rather than ship an SDE and re-derive the ME rounding and the four-part job-cost formula, let EVE Ref do the industry side.

```python
EVEREF = "https://api.everef.net/v1/industry/cost"

def industry_cost(product_id: int, runs: int = 1, me: int = 10, te: int = 20) -> dict:
    """ME-applied materials + total job-install cost for manufacturing product_id."""
    r = httpx.get(EVEREF, params={
        "product_id": product_id, "runs": runs, "me": me, "te": te,
    }, timeout=30)
    r.raise_for_status()
    return r.json()["manufacturing"][str(product_id)]
    # keys used: materials {type_id: {quantity, cost}}, total_job_cost, units, estimated_item_value
```

> **Gotcha:** EVE Ref prices materials on CCP's *adjusted prices* (correct for the job fee / EIV) — not what you'll pay at Jita. So we take only the **quantities** and **job cost** from EVE Ref and reprice the materials with Fuzzwork below.

### 1e. The profile and the fee maths (the Stage 2 seam)

This is the seam. The calculator never assumes skill levels — they arrive in a `Profile`. **Character skills do not reduce material quantities** (that's ME + rig bonuses); they affect fees and time.

```python
from dataclasses import dataclass

@dataclass
class Profile:
    """How you build — affects cost, fees, time. Stage 1: hand-entered.
    Stage 2: me/te from the blueprints endpoint, the skills from the skills endpoint."""
    me: int = 10               # blueprint material efficiency (0-10)
    te: int = 20               # blueprint time efficiency (0-20)
    accounting: int = 0        # reduces sales tax
    broker_relations: int = 0  # reduces broker fee
    industry: int = 0          # build time (used in Stage 2)
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
```

### 1f. Tie it together

```python
def evaluate_build(product_id: int, profile: Profile, hub: str = "jita", runs: int = 1) -> dict:
    from eve_constants import HUBS
    region = HUBS[hub]["region"]

    ind = industry_cost(product_id, runs=runs, me=profile.me, te=profile.te)
    materials = ind["materials"]                       # {type_id: {quantity, cost}}
    job_cost = float(ind["total_job_cost"])
    output_units = int(ind["units"])

    # Reprice materials + output at the hub in ONE Fuzzwork call
    mat_ids = [int(tid) for tid in materials]
    prices = hub_prices(mat_ids + [product_id], region_id=region)

    material_cost = sum(
        int(m["quantity"]) * sell_min(prices, int(tid)) for tid, m in materials.items()
    )
    sell_revenue = output_units * sell_min(prices, product_id)

    sales_tax = sell_revenue * sales_tax_rate(profile.accounting)
    broker_fee = sell_revenue * broker_fee_rate(profile.broker_relations)
    profit = sell_revenue - sales_tax - broker_fee - material_cost - job_cost
    total_cost = material_cost + job_cost

    return {
        "product_id": product_id, "hub": hub, "runs": runs, "profile": profile.label,
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
```

### ✅ Checkpoint 1

```python
# scratch.py
from eve_api import evaluate_build, price_history, resolve_type, Profile

rifter = resolve_type("Rifter")                       # name -> type_id
me_profile = Profile(me=10, accounting=5, broker_relations=4)
r = evaluate_build(rifter, me_profile, hub="jita")
print(r["profit"], r["margin"]); print(r["breakdown"])
print("history rows:", len(price_history(rifter, days=90)))
```

`python scratch.py` should resolve the Rifter, print a profit (positive or negative — frigates are thin), a sensible breakdown, and ~90 history rows. **Verify that first profit against Fuzzwork's blueprint calculator or in-game before trusting the chain.** Don't proceed until the number is believable.

---

## Part 2 — Wrap the logic as MCP servers

Expose the Part 1 logic over MCP. Two servers by responsibility: `market` (resolve + prices + history) and `industry` (evaluate_build). The MCP boundary stays JSON-flat — that's why the profile is passed as flat fields here, not a `Profile` object.

`servers/market_server.py`:

```python
# servers/market_server.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from mcp.server.fastmcp import FastMCP
from eve_api import price_history as _history, hub_prices as _prices, resolve_type as _resolve

mcp = FastMCP("market")

@mcp.tool()
def resolve(name: str) -> int:
    """Resolve an exact EVE item name to its type_id."""
    return _resolve(name)

@mcp.tool()
def price_history(type_id: int, region_id: int = 10000002, days: int = 90) -> list[dict]:
    """Daily min/avg/max + volume for an item in a region (last `days` days)."""
    return _history(type_id, region_id, days)

@mcp.tool()
def current_prices(type_ids: list[int], region_id: int = 10000002) -> dict:
    """Current buy/sell aggregates at a hub for a list of items."""
    return _prices(type_ids, region_id)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

`servers/industry_server.py`:

```python
# servers/industry_server.py
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from mcp.server.fastmcp import FastMCP
from eve_api import evaluate_build as _evaluate, Profile

mcp = FastMCP("industry")

@mcp.tool()
def evaluate_build(product_id: int, hub: str = "jita", runs: int = 1,
                   me: int = 10, te: int = 20, accounting: int = 0,
                   broker_relations: int = 0, label: str = "manual") -> dict:
    """Profit breakdown for building then selling an item at a hub.
    The profile fields arrive flat; Stage 2's character server fills them from ESI."""
    profile = Profile(me=me, te=te, accounting=accounting,
                      broker_relations=broker_relations, label=label)
    return _evaluate(product_id, profile, hub=hub, runs=runs)

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### ✅ Checkpoint 2

```bash
mcp dev servers/industry_server.py
```

The inspector UI opens; list tools and call `evaluate_build` — confirm it matches Checkpoint 1's shape. Do the same for `market_server.py` (try `resolve` with "Rifter"). Stdio servers are subprocesses; they start and stop with the client.

---

## Part 3 — Connect the servers from Python

`mcp_client.py` — load both servers' tools through the adapter:

```python
# mcp_client.py
import os
from langchain_mcp_adapters.client import MultiServerMCPClient

ROOT = os.path.dirname(__file__)

def make_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "market":   {"command": "python", "args": [os.path.join(ROOT, "servers", "market_server.py")],   "transport": "stdio"},
        "industry": {"command": "python", "args": [os.path.join(ROOT, "servers", "industry_server.py")], "transport": "stdio"},
    })
```

### ✅ Checkpoint 3

```python
# scratch_mcp.py
import asyncio
from mcp_client import make_client

async def main():
    tools = {t.name: t for t in await make_client().get_tools()}
    print("loaded:", list(tools))
    rid = await tools["resolve"].ainvoke({"name": "Rifter"})
    print(await tools["evaluate_build"].ainvoke({"product_id": rid, "hub": "jita", "accounting": 5}))

asyncio.run(main())
```

`python scratch_mcp.py` should list `resolve`, `price_history`, `current_prices`, `evaluate_build` and return a profit dict. You're now calling your own MCP servers as tools.

---

## Part 4 — The LangGraph graph

The graph: **resolve** names → type_ids → **evaluate** each (via MCP) → **rank** by margin → **report**. State is explicit and typed; the profile rides in state and is spread into the evaluate call (the seam).

`state.py`:

```python
# state.py
from typing import TypedDict, Optional

class AnalystState(TypedDict):
    watchlist: list             # item names OR type_ids
    hub: str
    profile: dict               # me, te, accounting, broker_relations, label
    min_margin: float
    resolved: list[int]
    evaluations: list[dict]
    shortlist: list[dict]
    approved: Optional[bool]
    report: Optional[str]
```

`graph.py`:

```python
# graph.py
import asyncio
from langgraph.graph import StateGraph, START, END
from mcp_client import make_client
from state import AnalystState

_tools = None
async def _get_tool(name: str):
    global _tools
    if _tools is None:
        _tools = {t.name: t for t in await make_client().get_tools()}
    return _tools[name]

async def resolve_items(state: AnalystState) -> dict:
    tool = await _get_tool("resolve")
    out = []
    for item in state["watchlist"]:
        out.append(item if isinstance(item, int) else await tool.ainvoke({"name": item}))
    return {"resolved": out}

async def evaluate(state: AnalystState) -> dict:
    tool = await _get_tool("evaluate_build")
    results = await asyncio.gather(*[
        tool.ainvoke({"product_id": tid, "hub": state["hub"], **state["profile"]})
        for tid in state["resolved"]
    ])
    return {"evaluations": results}

def rank(state: AnalystState) -> dict:
    good = [e for e in state["evaluations"]
            if e.get("margin") is not None and e["margin"] >= state["min_margin"]]
    good.sort(key=lambda e: e["margin"], reverse=True)
    return {"shortlist": good}

def report(state: AnalystState) -> dict:
    lines = [f"# Forge Analyst — {state['hub'].title()}", ""]
    for e in state["shortlist"]:
        b = e["breakdown"]
        lines.append(
            f"- **{e['product_id']}** · margin {e['margin']:.1%} · profit {e['profit']:,.0f} ISK  "
            f"(rev {b['sell_revenue']:,.0f} − mats {b['material_cost']:,.0f} "
            f"− job {b['job_cost']:,.0f} − tax {b['sales_tax']:,.0f} − broker {b['broker_fee']:,.0f})"
        )
    if not state["shortlist"]:
        lines.append("_No candidates cleared the margin threshold._")
    return {"report": "\n".join(lines)}

def build_graph(checkpointer=None):
    g = StateGraph(AnalystState)
    for name, fn in [("resolve", resolve_items), ("evaluate", evaluate),
                     ("rank", rank), ("report", report)]:
        g.add_node(name, fn)
    g.add_edge(START, "resolve")
    g.add_edge("resolve", "evaluate")
    g.add_edge("evaluate", "rank")
    g.add_edge("rank", "report")
    g.add_edge("report", END)
    return g.compile(checkpointer=checkpointer)
```

### ✅ Checkpoint 4

```python
# run.py
import asyncio
from graph import build_graph

async def main():
    out = await build_graph().ainvoke({
        "watchlist": ["Rifter", "Slasher", "Punisher"],     # names, resolved in-graph
        "hub": "jita",
        "profile": {"me": 10, "te": 20, "accounting": 5, "broker_relations": 4, "label": "manual"},
        "min_margin": 0.0,
    })
    print(out["report"])

asyncio.run(main())
```

`python run.py` should resolve the names and print a margin-sorted report. The whole pipeline runs end to end.

> **Note on "agent":** this is a deterministic graph — no LLM chooses anything, which is right for the maths-heavy core. The LLM earns its place later (natural-language watchlists, written rationales — a Stage 3 stretch). Keep the money maths deterministic.

---

## Part 5 — Human-in-the-loop + audit trail

Pause on the ranked shortlist for approval, and persist every run so it's resumable and auditable.

In `graph.py`, add an approval node and re-wire:

```python
from langgraph.types import interrupt, Command

def approve(state: AnalystState) -> dict:
    # interrupt() pauses and surfaces this payload. Code BEFORE interrupt() re-runs on
    # resume, so keep it side-effect free.
    decision = interrupt({"kind": "approve_shortlist", "shortlist": state["shortlist"],
                          "instructions": "Reply 'approve' to report, 'reject' to widen."})
    return {"approved": decision == "approve"}

def route_after_approve(state: AnalystState) -> str:
    return "report" if state["approved"] else "resolve"
```

Wire it between `rank` and `report` (remove the direct `rank -> report` edge):

```python
    g.add_node("approve", approve)
    g.add_edge("rank", "approve")
    g.add_conditional_edges("approve", route_after_approve,
                            {"report": "report", "resolve": "resolve"})
```

Run with a SQLite checkpointer and a `thread_id`. `run_hitl.py`:

```python
# run_hitl.py
import asyncio
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from graph import build_graph

INITIAL = {
    "watchlist": ["Rifter", "Slasher", "Punisher"],
    "hub": "jita",
    "profile": {"me": 10, "te": 20, "accounting": 5, "broker_relations": 4, "label": "manual"},
    "min_margin": 0.05,
}

async def main():
    with SqliteSaver.from_conn_string("checkpoints.sqlite") as cp:
        graph = build_graph(checkpointer=cp)
        cfg = {"configurable": {"thread_id": "session-1"}}

        result = await graph.ainvoke(INITIAL, config=cfg)      # stops at the interrupt
        for e in result["__interrupt__"][0].value["shortlist"]:
            print(f"  {e['product_id']}  margin {e['margin']:.1%}")

        decision = input("approve / reject> ").strip()
        final = await graph.ainvoke(Command(resume=decision), config=cfg)
        print("\n" + (final.get("report") or "(looped back to widen)"))

asyncio.run(main())
```

### ✅ Checkpoint 5

`python run_hitl.py` pauses, prints the shortlist, waits for input, then reports (`approve`) or loops back (`reject`). Prove the audit trail: kill the process while paused, rerun, and confirm it resumes `session-1` from the checkpoint. Every price, profile and margin the run touched is now in `checkpoints.sqlite`.

> Gate **high-value/irreversible** steps only — an approval gate adds unbounded latency, so don't gate every node.

---

## Part 6 — Charts and the final report

Add 90-day min/mean/max + volume charts. `charts.py`:

```python
# charts.py
import plotly.graph_objects as go
from eve_api import price_history

def history_chart(type_id: int, region_id: int = 10000002, days: int = 90) -> str:
    rows = price_history(type_id, region_id, days)
    dates = [r["date"] for r in rows]
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=dates, y=[r["highest"] for r in rows], name="max"))
    fig.add_trace(go.Scatter(x=dates, y=[r["average"] for r in rows], name="mean"))
    fig.add_trace(go.Scatter(x=dates, y=[r["lowest"]  for r in rows], name="min"))
    fig.add_trace(go.Bar(x=dates, y=[r["volume"] for r in rows], name="volume",
                         yaxis="y2", opacity=0.3))
    fig.update_layout(title=f"Type {type_id} — {days}d price",
                      yaxis2=dict(overlaying="y", side="right", showgrid=False))
    return fig.to_html(full_html=False, include_plotlyjs="cdn")
```

Extend the `report` node to embed a chart per shortlisted item and write `report.html`: pull the hub's region from `eve_constants.HUBS`, loop `state["shortlist"]`, and concatenate `history_chart(...)` under each item's breakdown.

### ✅ Checkpoint 6

Run the full flow and open `report.html`: a ranked list of builds, each with its cost breakdown and an interactive 90-day chart. That's Stage 1 complete.

---

## Part 7 — Switch on the ingest (Stage 3 prep)

The demand model is Stage 3, but it can't train on data you didn't capture — so start collecting now. `ingest.py` streams killmails from zKillboard's RedisQ into DuckDB. Run it in the background and forget it; nothing reads the store yet.

```python
# ingest.py  —  run:  python ingest.py   (leave it running)
import time, json, httpx, duckdb

QUEUE_ID = "forge-analyst-CHANGE-ME"           # any unique string; RedisQ remembers you
REDISQ = "https://zkillredisq.stream/listen.php"

db = duckdb.connect("intel.duckdb")
db.execute("""CREATE TABLE IF NOT EXISTS killmails (
    killmail_id BIGINT, killmail_time TIMESTAMP, solar_system_id INTEGER, raw JSON)""")

def poll_once() -> int:
    pkg = httpx.get(REDISQ, params={"queueID": QUEUE_ID}, timeout=15).json().get("package")
    if not pkg:                                 # null package = nothing new in 10s
        return 0
    km = pkg["killmail"]
    db.execute("INSERT INTO killmails VALUES (?, ?, ?, ?)",
               [km["killmail_id"], km["killmail_time"], km["solar_system_id"], json.dumps(pkg)])
    return 1

if __name__ == "__main__":
    print("ingesting killmails -> intel.duckdb (Ctrl-C to stop)")
    while True:
        try:
            poll_once()
        except Exception as e:
            print("ingest error:", e); time.sleep(5)
```

Stage 3 will resolve `solar_system_id` → region and explode each killmail's `victim.items` into destroyed quantities per `type_id` — the replacement-demand signal. For now, just let it accumulate. (RedisQ allows one connection per queue ID; don't run two copies.)

### ✅ Checkpoint 7

`python ingest.py`, leave it a few minutes, then in another shell:

```python
import duckdb
print(duckdb.connect("intel.duckdb").execute("SELECT count(*) FROM killmails").fetchone())
```

A non-zero and growing count means history is accumulating.

---

## Part 8 — LangGraph Studio (optional)

`langgraph.json`:

```json
{ "dependencies": ["."], "graphs": { "forge": "./graph.py:build_graph" } }
```

`pip install "langgraph-cli[inmem]"` then `langgraph dev` opens Studio to watch state flow node to node and see where it pauses.

---

## Where to go next

This is Stage 1. The next two stages (in `ROADMAP.md`) reuse everything here:

- **Stage 2 — character data.** Add an authenticated `character` MCP server (EVE SSO + PKCE) returning the **same `Profile` shape** from real skills and blueprints, across multiple characters. The graph gains a step that picks a character's profile; `evaluate_build` is untouched (the seam pays off). Adds proper build-time from Industry/Advanced Industry skills.
- **Stage 3 — demand predictor.** The `intel` server reads the DuckDB history you've been accumulating since Part 7, builds a lagged demand signal, and a graph node biases the ranking. Plus the optional LLM layer.

## Gotchas worth re-reading

- **Recipe data is in the SDE, not ESI** — that's why we lean on EVE Ref.
- **EIV ≠ what you pay** — job cost on adjusted prices, materials on hub prices; keep them distinct.
- **Skills affect fees + time, not material quantities.**
- **SCC surcharge is 4% and fixed** — EVE Ref already includes it in `total_job_cost`.
- **ESI etiquette** — real `User-Agent`, cache history (changes once a day), respect the error limit.
- **interrupt() re-runs the node from the top on resume** — side effects go *after* the interrupt.
- **Keep graph state lean** — it's serialized on every checkpoint write.
