# installed_plists fixtures

Canonical installed-plist fixtures captured 2026-05-06 (cycle 10) from
`~/Library/LaunchAgents/` under operator approval. All 6 fixtures are
byte-faithful to the live installed plists, with only IC §15 Rule 2 token
normalizations applied (`@REPO_ROOT@`, `@VENV_PYTHON@`, `@GOVERNANCE_LLM_MODEL@`).

## Fixtures present

- `com.jake.kalshi-bot.plist`
- `com.jake.kalshi-bothealth.plist`
- `com.jake.kalshi-soak-check.plist`
- `com.kalshi.db-backup.plist`
- `com.kalshi.governance.fast.plist`
- `com.kalshi.governance.deep.plist`

## Audit modes

```bash
# Live installed-state equivalence (operator-machine)
.venv/bin/python scripts/launchd_template_equivalence_audit.py --installed

# Canonical fixture equivalence (CI / cross-machine)
.venv/bin/python scripts/launchd_template_equivalence_audit.py --fixtures

# Both
.venv/bin/python scripts/launchd_template_equivalence_audit.py --installed --fixtures
```

## Refresh procedure

If the templates change (intentional improvement per IC §15 Rule 4) AND
the operator has applied the change to installed plists via
`bash ops/launchd/install.sh`, refresh fixtures:

```bash
.venv/bin/python -c "
import sys
sys.path.insert(0, 'scripts')
from launchd_template_equivalence_audit import Paths, normalize, _repo_root, DEFAULT_LABELS
from pathlib import Path
import os

repo_root = _repo_root()
paths = Paths(
    repo_root=repo_root,
    venv_python=repo_root / '.venv/bin/python',
    template_dir=repo_root / 'ops/launchd',
    installed_dir=Path.home() / 'Library/LaunchAgents',
    fixtures_dir=repo_root / 'tests/fixtures/installed_plists',
    governance_model=os.environ.get('GOVERNANCE_LLM_MODEL', 'qwen3:14b'),
)
for label in DEFAULT_LABELS:
    raw = (paths.installed_dir / f'{label}.plist').read_text()
    (paths.fixtures_dir / f'{label}.plist').write_text(normalize(raw, paths))
"
```

Commit the fixture refresh in the SAME commit as the template change per IC §15 Rule 4.

Do not synthesize fixture contents from README/spec intent. Capture must be
byte-faithful to installed LaunchAgents.
