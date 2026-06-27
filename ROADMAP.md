# Forge Analyst — staged roadmap

Three product stages, each independently useful and demoable, with the MCP + LangGraph scaffolding present from Stage 1 by design. This sits alongside the architecture (`PLAN.md`) and the Stage 1 build walkthrough (`TUTORIAL.md`).

The dependency flow is deliberate: build the calculator, enrich it with real character data, then add the predictive layer. Each stage builds on the last without rewriting it — provided three seams are respected from the start (see below).

---

## The three seams (decide once, save rework everywhere)

1. **Skills-parametric calc.** Every cost/fee/time function takes a *skills profile* as an argument — never assumes skill levels internally. In Stage 1 that profile is hand-entered or defaulted; in Stage 2 the same functions receive a profile pulled from ESI. Stage 2 becomes a change of *source*, not a rewrite.
2. **Framework-first scaffolding.** Even Stage 1's single-item lookup runs through MCP servers behind a minimal LangGraph graph. The frameworks are the point of the project; we grow into them rather than retrofit them.
3. **Ingest-early.** Stage 3's demand model needs accumulated history. The *model* is Stage 3, but the *ingest* (a cheap background logger writing killmails + system activity to DuckDB) switches on in Stage 1, so you reach Stage 3 with months of data instead of zero.

---

## Stage 1 — Industry calculator on the scaffolding

**Goal:** search for an item, get a full build-cost and profit breakdown at a hub, with a 90-day price chart — computed through MCP tools orchestrated by LangGraph.

**Build:**
- *Name → typeID resolution* — the front door. ESI `/universe/ids/` (POST names → ids) or the Fuzzwork type dump. Wrap as a `resolve` tool. Easy to overlook; it gates everything else.
- *`market` MCP server* — current hub prices (Fuzzwork) + 90-day min/mean/max + volume history (ESI).
- *`industry` MCP server* — `evaluate_build`: EVE Ref for ME-applied quantities + job cost, repriced at the hub, fees applied. **Takes a skills profile argument** (seam 1).
- *Minimal LangGraph graph* — even single-item goes `resolve → evaluate → report` through the graph, with the SQLite checkpointer recording every run (audit trail from day one). Then grow to `watchlist → evaluate → rank → human-approve → report` as Stage 1b.
- *Charts* — the 90-day history view per item.
- *Stage 3 ingest stub* — stand up the background logger now (seam 3); it just needs to be writing to DuckDB, nothing reads it yet.

**Framework touchpoints:** FastMCP servers, `MultiServerMCPClient`, `StateGraph`, checkpointer, `interrupt()` (Stage 1b).

**Definition of done:** search "Rifter" → ranked cost/profit breakdown + chart, produced via the MCP servers under LangGraph, with the run persisted in the checkpointer. (The `TUTORIAL.md` build covers this stage — fold in name-resolution, the skills-profile signature, and the ingest stub as the three deltas.)

**Deferred:** real character data, demand model, T2 invention, multi-hub, LLM.

---

## Stage 2 — Character enrichment (multi-character)

**Goal:** replace the hand-entered skills profile with live ESI data across multiple characters, and use it to compute time, costs and profit properly.

**Build:**
- *`character` MCP server (authenticated)* — owns the EVE SSO / PKCE flow and the multi-character token store. Read-only scopes only; refresh tokens stored outside the repo.
- *Pulls:* skills (`esi-skills.read_skills.v1`) → Accounting + Broker Relations feed fees, Industry + Advanced Industry feed *time*; blueprints (`esi-characters.read_blueprints.v1`) → real ME/TE and which blueprints you actually own; standings (`esi-characters.read_standings.v1`) → the missing piece for an exact broker fee. Optional: wallet, assets, market orders, industry jobs for budget/inventory awareness.
- *Graph gains a `profile` step* — selects a character's profile and hands it to the **same** `evaluate_build` (seam 1 paying off — no calc rewrite). Profile keyed by `character_id`, so multi-character is just "which profile."
- *Build time* — now computed properly from Industry/Advanced Industry skills + blueprint TE (time wasn't in Stage 1's core).

**Framework touchpoints:** authenticated MCP server, OAuth token refresh lifecycle, per-character graph state.

**Security:** PKCE (no client secret in the codebase), read-only scopes, refresh tokens gitignored / in a config dir. Note: changing requested scopes later forces a re-login.

**Definition of done:** authorise 2+ characters, evaluate the same item using each one's real skills and blueprints, and see how skill differences move fees, time and margin. The Stage 1 ingest has been quietly accumulating history throughout.

**Deferred:** the demand model.

---

## Stage 3 — Demand predictor

**Goal:** turn the accumulated destruction + activity history into a replacement-demand signal that biases the ranking — a "what's about to get pricier" view.

**Build:**
- *History* — the DuckDB store has been filling since Stage 1 (seam 3), so there's real data to model.
- *`intel` MCP server* — `destroyed_volume(type_id, region, days)` and `system_activity(region)`, reading the local store.
- *The model* — a panel keyed `type_id × region × day`; features = lagged destroyed quantity (zKillboard), system kills/jumps (ESI), and lagged price + volume; target = forward Δprice or Δvolume. Start narrow: one ammo type, The Forge plus neighbours.
- *Graph gains a demand node* — attaches a demand score to each candidate and can re-rank or flag.
- *Rigour* — validate against the confounders already mapped: supply response (wars spike manufacturing too), lag structure, a liquidity gate, and zKillboard's coverage bias. Report uncertainty; an honest "no usable signal on this item class" is a valid result.

**Framework touchpoints:** the third MCP server, plus the offline ingest pipeline as the architecture's deliberate second limb (stateful, scheduled — unlike the on-demand calculator).

**Definition of done:** a backtested signal, surfaced in the report, that demonstrably does (or honestly does not) lead price on the chosen item class.

**Deferred / stretch:** the LLM layer (natural-language watchlists, written rationales), multi-hub arbitrage, T2 invention.

---

## At a glance

| | Stage 1 | Stage 2 | Stage 3 |
|---|---|---|---|
| New MCP server | market, industry | character (auth) | intel |
| Skills profile | hand-entered | live, multi-char | (unchanged) |
| Computes | cost, profit, charts | + time, exact fees | + demand signal |
| Graph shape | resolve → evaluate → report (→ rank/approve) | + profile step | + demand node |
| Running since S1 | — | ingest stub | ingest now feeds model |
| Frameworks exercised | FastMCP, StateGraph, checkpointer, interrupt | authed MCP, token lifecycle | offline pipeline, 3-server orchestration |
