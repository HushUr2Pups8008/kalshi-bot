# installed_plists fixtures

Canonical installed-plist fixtures belong here when captured under explicit
operator approval.

Current local live equivalence is covered by:

```bash
.venv/bin/python scripts/launchd_template_equivalence_audit.py --installed
```

Do not synthesize fixture contents from README/spec intent. Capture must be
byte-faithful to installed LaunchAgents, with only audit-time token
normalization applied for comparison.
