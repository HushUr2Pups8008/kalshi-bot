# kalshi-bot

[![Version](https://img.shields.io/badge/version-0.37.50-blue)](CHANGELOG.md)
[![Mode](https://img.shields.io/badge/default-paper%20trading-orange)](.env.example)

Paper research for Kalshi event markets, plus a small live politics desk.
The operated desks call xAI Grok. Tracking lives in
[docs/profit_path_debt_log.md](docs/profit_path_debt_log.md).

## What runs

- Politics desk, launchd label `com.jake.kalshi-bot-politics`, entry `main.py`. Live only for series the universe marks live. Ask bands and size stay at the current gates.
- Grok paper sidecar, label `com.jake.kalshi-grok-paper`, entry `scripts/grok_paper_sidecar.py`. It forces paper mode, keeps both Polymarket order flags false, and blocks `POST /v1/orders`. The hard cap is $3. Paper series and scan calls stop at the greater of $0.75 and 25% of trailing 7-day settled fee-net, and leave $1 for exit when that cap is above $1.
- Count shadow. `KXTRUTHSOCIAL` and `KXTRUMPACT` get a free count probability beside the ask. It is not a queue and it is not promoted.
- Maker shadow. Each paper candidate log stores a resting bid one cent under the ask, stopped at 36c. No order is sent.
- Polymarket is paper views only. Series keys that start with `PM` stay off until 2026-11-03 UTC.

Do not install or restart the retired label `com.jake.kalshi-bot`.

## Path

Free book filters run before a paid Grok call: the ask band, the 55c YES floor, the 91c gate, an empty ask, the blocklist, and the series universe. Paid calls use `https://api.x.ai/v1` and the model `grok-4.20-0309-non-reasoning`. A Polymarket paper row still needs a fee-net edge and two stable scans; the operated sidecar turns Kalshi series and tie-break calls off. The exit watch logs the bid and ask and does not call Grok. The live veto is a separate yes or no. It does not apply that stability rule.

Signing is RSA-PSS with the digest salt length. Open-market requests use `status=open`.

## Layout

| Path | Role |
| --- | --- |
| `main.py` | Politics desk |
| `scripts/grok_paper_sidecar.py` | Paper Grok scan |
| `utils/event_news_grok_edge.py` | Paper queue and spend |
| `utils/event_news_grok_veto.py` | Live yes/no veto |
| `utils/countable_series_shadow.py` | Free count probability |
| `utils/event_news_maker_shadow.py` | Hypothetical resting bid |
| `trading/` | Orders, fees, paper books |
| `kalshi/` | REST client |
| `config/series_universe.yaml` | Series modes |
| `governance/` | Review agent, separate from the desks |

Older research code can still call a local model. The desks above do not.

## Setup

Python 3.11, as in `.python-version`.

```bash
python3.11 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
```

The Kalshi PEM is one line with literal `\n` sequences. Leave `LIVE_TRADING_ENABLED` false unless the operator turns it on.

## Checks and restarts

```bash
make lint
.venv/bin/pytest
```

CI runs lint, tests, sims-smoke, and p0-gate. Restart a desk only after a runtime merge, and only that process. Do not restart politics while it holds a position or a resting order.

## Safety

Paper is the default. Do not lower the ask band, the size cap, or the fee-net gate to force trades. Keep `.env`, logs, and databases out of commits.
