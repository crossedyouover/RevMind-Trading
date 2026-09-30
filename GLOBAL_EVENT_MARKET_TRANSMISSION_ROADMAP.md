# Global Event & Market Transmission Intelligence Roadmap

Status: **approved architecture-harvest and future integration track; not an implemented trading signal or execution capability**.

## Purpose

RevMind Trading should eventually reason about relevant events outside the price chart without
turning external OSINT systems into a second source of trading authority.

This track introduces a future RevMind-owned boundary for:

```text
global event -> transmission path -> exposed markets/assets -> cross-asset evidence
             -> setup/portfolio context -> deterministic risk -> Head of Desk
```

The goal is not to copy an OSINT terminal. It is to convert explicitly sourced, point-in-time
global facts into auditable market context.

## World Monitor architecture harvest

World Monitor is a useful benchmark and possible external intelligence provider because its hosted
interfaces expose global-intelligence data spanning conflict, country risk, sanctions, markets,
commodities, energy, maritime/aviation activity, cyber threats, natural disasters, prediction
markets, and forecasts.

RevMind should study/adapt these architectural ideas:

- broad provider adapters remain at the external edge;
- global events are separate from market conclusions;
- source attribution and freshness are first-class;
- country/risk, chokepoint, infrastructure, energy, maritime and cross-asset context can be queried
  independently;
- machine clients should use bounded structured interfaces rather than scrape the visual dashboard;
- external intelligence can enrich a decision without owning the decision.

World Monitor must remain an **external benchmark/provider**, not a RevMind core dependency.

## License and product boundary

World Monitor's platform source is AGPL-3.0-only. RevMind Trading must not copy, vendor, translate,
or derive proprietary implementation code, UI/trade dress, or internal source architecture from
that codebase without a separate license review.

World Monitor's hosted API/MCP products have their own subscription and output-use terms. Any
production or commercial integration must review the applicable plan, redistribution class,
retention rules, source-specific notices, and excluded data before activation.

Accordingly:

```text
ALLOWED NOW
- study public architecture and documentation
- record independent RevMind requirements
- benchmark concepts and interfaces

POSSIBLE LATER, AFTER LICENSE/TERMS REVIEW
- consume World Monitor through its official REST API / MCP service
- use permitted structured outputs as external observations

NOT ALLOWED BY THIS ROADMAP
- copy World Monitor source into RevMind
- make World Monitor required for RevMind to operate
- bypass RevMind ingestion/PIT/risk boundaries
- redistribute World Monitor data merely because it is technically accessible
```

## Proposed RevMind-owned domains

Names are provisional until a dedicated design phase freezes contracts.

```text
app/intelligence/
    events/
    geopolitical/
    macro/
    cross_asset/
    exposure/
    transmission/
```

The implementation should reuse existing canonical/PIT concepts where possible rather than create
an unrelated intelligence stack.

## Intelligence flow

```text
External intelligence providers
          |
provider-specific adapters
          |
canonical global-event observations
          |
append-only knowledge-time boundary
          |
PIT global-event materialization
          |
transmission / exposure analysis
          |
typed global-market evidence
          |
research + setup + portfolio context
          |
DETERMINISTIC RISK VETO
          |
future Head of Desk
```

World Monitor, direct public sources, or future providers may sit behind the first boundary. None
is authoritative downstream merely because it supplied an event.

## Candidate event domains

Future design may include:

- geopolitical conflict and escalation;
- sanctions and export controls;
- maritime chokepoints and shipping disruption;
- aviation disruption when economically relevant;
- energy supply and infrastructure disruption;
- natural disasters and severe weather;
- cyber incidents affecting material infrastructure/companies;
- central-bank and macro-policy events;
- country instability/risk changes;
- supply-chain disruption;
- prediction-market changes as a separately attributed source;
- cross-asset confirmation in rates, FX, commodities, volatility, indices and safe-haven assets.

## Transmission graph

RevMind should distinguish an observed event from a hypothesized market transmission.

Example:

```text
chokepoint disruption
      |
shipping capacity / insurance / transit time
      |
energy or goods supply
      |
commodity prices / inflation expectations
      |
rates / FX / sector effects
      |
instrument exposure
```

A transmission edge must carry provenance and an explicit basis. An LLM may later explain or
propose relationships, but it must not silently convert speculation into deterministic fact.

## Candidate evidence

Exact keys are deliberately not frozen here. Candidate descriptive evidence includes:

```text
GEOPOLITICAL_STRESS_RISING
SHIPPING_CHOKEPOINT_DISRUPTION
ENERGY_SUPPLY_RISK
COUNTRY_INSTABILITY_RISING
INFRASTRUCTURE_DISRUPTION
CENTRAL_BANK_POLICY_SHIFT
CROSS_ASSET_RISK_OFF
SAFE_HAVEN_DEMAND_RISING
SUPPLY_CHAIN_RISK
PREDICTION_MARKET_PROBABILITY_CHANGE
```

These are context/evidence, not BUY/SELL instructions.

## Point-in-time requirements

Every future implementation must preserve RevMind's historical-knowledge guarantees:

1. source publication/event time and RevMind observation time remain separate;
2. a fact cannot affect an evaluation before RevMind could have known it;
3. revisions/corrections remain auditable rather than rewriting history;
4. transmission evidence records which event observations and market observations supported it;
5. historical evaluation uses only information available at the requested cutoff;
6. unknown timestamps are not guessed;
7. missing provider data is not silently replaced by a different source;
8. source disagreement remains visible unless an explicit RevMind reconciliation rule exists.

## External-provider resilience

World Monitor should be one possible provider, never a mandatory runtime dependency.

The future provider boundary should support:

```text
AVAILABLE
DEGRADED
RATE_LIMITED
UNAVAILABLE
NOT_CONFIGURED
```

If World Monitor is unavailable, RevMind's market-data, deterministic analysis, risk, journal,
paper/shadow and other independent capabilities must continue operating.

## MCP / API / Angelo OS boundary

A future architecture may expose both RevMind Trading and World Monitor to Angelo OS/Jarvis, but
authority must remain explicit:

```text
Angelo OS / Jarvis
      |
      +---- RevMind Trading governed adapter/MCP
      |
      +---- World Monitor MCP/API (external intelligence)
                    |
                    v
          evidence proposed to RevMind
                    |
             RevMind validation/PIT
                    |
             deterministic risk
```

Angelo OS must not use World Monitor to bypass RevMind risk, permissions, execution mode, portfolio
limits, or audit requirements.

## Validation

Before any global-intelligence evidence influences a setup, ranking, portfolio decision, or alert,
RevMind must evaluate:

- source reliability and timestamp semantics;
- data completeness and revision behavior;
- false-positive/false-correlation risk;
- lag from event to observation;
- stability of transmission mappings;
- incremental value over market-only baselines;
- behavior during historical crises using strict PIT replay;
- provider outages and conflicting sources.

Narrative plausibility is not evidence of predictive value.

## First implementation slice -- explicit exclusions

Do not add yet:

- live World Monitor credentials;
- World Monitor SDK/source dependency;
- automated MCP calls;
- autonomous web research;
- geopolitical BUY/SELL signals;
- LLM-created facts treated as observations;
- automatic portfolio changes;
- automatic or real-money execution;
- copied third-party UI;
- hundreds of direct data integrations.

## Implementation sequence

After a dedicated design/adversarial review:

```text
A. freeze canonical global-event observation contracts — implemented in Phase 102
B. freeze source/time/revision semantics — implemented in Phase 103
C. build one narrow external-provider adapter
D. append-only global-event storage + PIT materialization
E. define deterministic exposure/transmission contracts
F. add cross-asset evidence with explicit provenance
G. evaluate against market-only baselines
H. expose read-only API/control-plane views
I. add UI/global-intelligence presentation
J. consider World Monitor MCP/API production integration after license review
```

This roadmap intentionally keeps global intelligence subordinate to RevMind Trading's canonical
knowledge model, deterministic risk authority, and auditable decision process.
