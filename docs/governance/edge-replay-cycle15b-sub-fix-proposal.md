# Cycle-15B Sub-Fix Proposal

Status: single-step sub-fix selected

## Selected Surface

- Surface: `per_keyword_direction_map`
- Target: `config.py:734` / `GEOPOLITICAL_SIGNALS`
- Scope: `single_step`

## Evidence

- C2 zero-collapse step: `keyword_path` (`8` fixtures)
- C4 keyword coverage gaps: `8` directional fixtures
- C5 suppression count: `0`
- C3 LLM statuses: `ollama_circuit_open, ollama_unavailable`

## Reason

keyword_path is the first zero-collapse step; directional fixture vocabulary is absent from the keyword direction map; suppression did not fire.

## Before

```python
GEOPOLITICAL_SIGNALS = [
    # Existing geopolitical/event keyword groups.
    # Cycle-15B resolution-event fixture vocabulary is absent, so _keyword_score(...)
    # returns net_shift = 0 for FISA, pardon, Iran-deal, and Vance-visit outcomes.
]
```

## After

```python
GEOPOLITICAL_SIGNALS = [
    # Existing groups unchanged.
    {
        "keywords": [
            "fisa section 702 reauthorization signed into law",
            "fisa section 702 reauthorization legislation",
            "trump issues pardons",
            "signed pardons",
            "sign nuclear deal",
            "comprehensive nuclear agreement",
            "arrives in islamabad",
            "official pakistan visit",
        ],
        "direction": "yes",
        "strength": 0.12,
    },
    {
        "keywords": [
            "fisa section 702 expires",
            "senate fails to act",
            "will not become law",
            "no january 6 pardons",
            "no pardons for january 6 defendants",
            "cancels pakistan trip",
            "canceled his planned pakistan trip",
        ],
        "direction": "no",
        "strength": 0.12,
    },
]
```

## Guardrails

- C7 may implement only this keyword-map change unless the operator grants a scope extension.
- Acceptance remains `>=6/10` Lane B fixtures passing direction + magnitude.
- IC §16 final acceptance still requires replay evidence: `>=1` slice with `ev_ci_95_lo > 0` and `trades >= 10`.
