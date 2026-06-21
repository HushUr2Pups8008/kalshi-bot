"""Canonical geopolitical named-entity set — single source of truth.

This is the named-entity roster (countries, demonyms, key political figures,
institutions) shared by ``analysis/market_matcher`` (news→market gate boost) and
``analysis/market_specificity`` (named-entity-density sub-score). It previously
lived as two hand-maintained copies; the figure roster is the most rot-prone
part (it changes when an administration turns over), so keeping it in one place
ensures a roster update can never silently diverge between the two consumers.

Leaf module by design — it imports nothing from ``analysis`` so both consumers
can import it without a cycle (``market_matcher`` already imports
``market_specificity``). ``market_specificity`` adds a few institutional tokens
locally; those are intentionally NOT added here because including them in the
matcher gate set could change gate behavior.

⚠️ This roster is still hand-curated and tied to the current geopolitical moment
(see the rot-surface inventory). A durable follow-on is to derive prominent
figures from market-title frequency; until then, edit this ONE list.
"""

from __future__ import annotations

GEO_NAMED_ENTITIES: frozenset[str] = frozenset({
    # Country names
    "ukraine", "russia", "china", "taiwan", "iran", "israel", "gaza",
    "korea", "pakistan", "india", "japan", "turkey", "saudi", "syria",
    "iraq", "afghanistan", "venezuela", "cuba", "mexico", "canada",
    "france", "germany", "britain", "lebanon", "hamas", "hezbollah",
    "brazil", "colombia", "philippines", "vietnam", "thailand",
    "egypt", "libya", "yemen", "somalia", "sudan", "ethiopia",
    # Adjective / demonym forms
    "russian", "chinese", "iranian", "ukrainian", "korean", "israeli",
    "european", "american", "british", "french", "german", "turkish",
    "japanese", "lebanese", "iraqi", "syrian", "saudi", "canadian",
    "mexican", "brazilian", "colombian", "egyptian",
    # Key individuals
    "zelensky", "zelenskyy", "putin", "trump", "biden", "netanyahu",
    "khamenei", "hegseth", "modi", "macron", "scholz", "starmer",
    "vance", "rubio", "waltz",
    # Institutions / orgs (distinctive enough for single-token match)
    "nato", "pentagon", "kremlin", "congress", "senate",
})
