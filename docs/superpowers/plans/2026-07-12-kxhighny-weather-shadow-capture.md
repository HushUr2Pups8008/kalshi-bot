# KXHIGHNY Weather Shadow Capture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a disabled-by-default, credential-free KXHIGHNY capture and outcome-labeling pipeline that records deterministic fixed-horizon weather/quote ladders in an isolated append-only database without influencing research admission or trading.

**Architecture:** Keep pure validation and hashing separate from network readers, orchestration, and SQLite ownership. Use public GET-only Kalshi/NWS clients, one isolated `weather_shadow.db`, five append-only tables, atomic complete-ladder writes, and a lazy runtime factory. Capture and label loops share no research-prewarm quota and never import executor/order/account surfaces.

**Tech Stack:** Python 3.12, `asyncio`, `aiohttp`, `sqlite3`, `zoneinfo`, `decimal.Decimal`, frozen dataclasses, pytest, Ruff, existing launchd/botcheck/backup infrastructure.

## Global Constraints

- Approved design: `docs/superpowers/specs/2026-07-12-kxhighny-weather-shadow-capture-design.md`; its schema fields and invariants are normative.
- `ENABLE_WEATHER_SHADOW_CAPTURE` defaults to `false`. Disabled means no weather-module import, object construction, network call, SQLite connect, DDL, file/journal creation, or task.
- The feature is shadow-only. No probability output, trade candidate, research promotion, admission, blend, cache, sizing, executor, paper, order, balance, position, or account dependency is permitted.
- Kalshi access is credential-free and GET-only. NWS access is GET-only with a descriptive User-Agent.
- The only persistence target is `data/weather_shadow.db`; never connect to or mutate `data/evidence_store.db` or `data/paper_trades.db`.
- Capture cadence is 300 seconds, concurrency one, at most two events per cycle, eight seconds per request, and 20 seconds for network/build work. Persistence starts only after that budget succeeds and is shielded and awaited through cancellation.
- All five tables are append-only with `BEFORE UPDATE` and `BEFORE DELETE` abort triggers. Foreign keys are enabled on every connection.
- Initial deployment leaves the flag disabled. Editing the LaunchAgent to enable capture is a separate explicit operator gate, not implied by approval of this plan.
- Runtime artifacts `data/matcher_token_weights.json`, `logs/backups/`, and `logs/state/` remain unstaged and unchanged.

---

## Task 1: Define Pure Domain Types And Canonical Validation

**Files:**
- Create: `weather/__init__.py`
- Create: `weather/shadow_models.py`
- Create: `tasks/kxhighny_shadow_validation.py`
- Create: `tests/test_kxhighny_shadow_validation.py`

- [ ] Write RED tests for `parse_event_target_date()` using real KXHIGHNY ticker forms, malformed tickers, and New York civil dates across DST boundaries.
- [ ] Write RED tests for horizon selection. `T-24h`, `T-12h`, `T-6h`, and `T-1h` are claimable only by the first cycle in `[horizon - 900 seconds, horizon]`; already-claimed, early, late, and post-close cycles return no claim.
- [ ] Write RED tests for exact Celsius-to-Fahrenheit conversion with `Decimal`, quantized to `0.1` using `ROUND_HALF_UP`.
- [ ] Write RED tests for weather derivation: grid max-temperature valid intervals overlapping the New York target day, hourly starts inside that day, KNYC observations measured inside that day and no later than `as_of`, QC rejection, forecast spread, and weekday.
- [ ] Write RED tests proving missing, ambiguous, revised-without-identity, future-issued, future-measured, or failed-QC weather inputs are ineligible.
- [ ] Write RED ladder tests for exact lower/upper bounds, two tails, contiguous non-overlapping ranges, common close/status/rules/settlement source, complete event enumeration, one-hot settlement, valid cents, non-crossed books, complement consistency, and fixed-point sizes.
- [ ] Write RED timing tests rejecting weather retrieved after quote retrieval, feature availability after `as_of`, `as_of >= close`, a capture sweep over ten seconds, and any target-date mismatch.
- [ ] Define frozen DTOs with `Decimal` numeric values and timezone-aware `datetime` values:

```python
HorizonBucket = Literal["T-24h", "T-12h", "T-6h", "T-1h"]

@dataclass(frozen=True)
class Fingerprints:
    contract: str
    rules_source: str
    settlement_source: str

@dataclass(frozen=True)
class WeatherFeatures:
    forecast_issued_at: datetime
    forecast_valid_start: datetime
    forecast_valid_end: datetime
    observation_measured_at: datetime
    observation_coverage_start: datetime
    observation_count: int
    weather_retrieved_at: datetime
    grid_forecast_high_f: Decimal
    hourly_forecast_high_f: Decimal
    running_observed_high_f: Decimal
    forecast_spread_f: Decimal
    target_weekday: int
    source_payload_json: str
    source_payload_hash: str

@dataclass(frozen=True)
class ShadowQuote:
    market_ticker: str
    close_time: datetime
    lower_bound_f: int | None
    upper_bound_f: int | None
    is_lower_tail: bool
    is_upper_tail: bool
    fingerprints: Fingerprints
    yes_bid_cents: int
    yes_ask_cents: int
    no_bid_cents: int
    no_ask_cents: int
    yes_bid_size: Decimal
    yes_ask_size: Decimal
    no_bid_size: Decimal
    no_ask_size: Decimal
    last_price_cents: int | None
    volume: Decimal | None
    price_retrieved_at: datetime
    raw_payload_hash: str

@dataclass(frozen=True)
class CaptureBatch:
    snapshot_id: str
    capture_key: str
    event_ticker: str
    target_date: date
    capture_started_at: datetime
    capture_finished_at: datetime
    as_of: datetime
    close_time: datetime
    seconds_to_close: Decimal
    horizon_bucket: HorizonBucket
    features: WeatherFeatures
    quotes_hash: str
    fee_schedule_version: str
    model_version: str
    quotes: tuple[ShadowQuote, ...]
```

- [ ] Own all transport-neutral DTOs in `weather/shadow_models.py`, including `RetrievedEvent`, `RetrievedMarket`, `NwsCapturePayloads`, `NwsDailyLabel`, capture DTOs, and outcome DTOs. Pure validation, public readers, capture orchestration, and the store import inward from this module; readers and validation never import each other.
- [ ] Define immutable provenance in `weather/shadow_models.py`: `WEATHER_SHADOW_CAPTURE_MODEL_VERSION = "kxhighny-fixed-horizon-v1"`; a normalized static fee-schedule record verified from the official Kalshi source; and `WEATHER_SHADOW_FEE_SCHEDULE_VERSION` as the canonical SHA-256 of that record. Store the version in every snapshot and cover the source URL/hash in tests.

- [ ] Implement these pure entry points with typed fail-closed exceptions:

```python
def parse_event_target_date(event_ticker: str) -> date: ...
def select_due_horizon(
    now: datetime,
    close: datetime,
    claimed: AbstractSet[HorizonBucket],
) -> HorizonBucket | None: ...
def derive_weather_features(
    payloads: NwsCapturePayloads,
    target: date,
    as_of: datetime,
) -> WeatherFeatures: ...
def normalize_complete_ladder(
    event: RetrievedEvent,
    target_date: date,
) -> tuple[ShadowQuote, ...]: ...
def validate_one_hot_ladder(quotes: Sequence[ShadowQuote]) -> None: ...
def validate_capture_timing(batch: CaptureBatch, max_sweep: timedelta) -> None: ...
def build_capture_batch(...) -> CaptureBatch: ...
```

- [ ] Canonical JSON uses sorted keys, explicit decimal strings, and UTC ISO timestamps. `snapshot_id`, `capture_key`, `quotes_hash`, and payload hashes include every evaluation input, including fee-schedule/model versions and all three fingerprints.
- [ ] Inject the immutable model and fee-schedule versions into `build_capture_batch()` and `WeatherShadowCaptureTask`; tests prove changing either version changes the correct capture/snapshot identity.
- [ ] Ensure the module has no research, store, config, trading, executor, or authenticated-client imports.
- [ ] Run `.venv/bin/python -m pytest tests/test_kxhighny_shadow_validation.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check tasks/kxhighny_shadow_validation.py tests/test_kxhighny_shadow_validation.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: define deterministic weather shadow records"`.

## Task 2: Create The Exact Append-Only Schema And Lazy Store

**Files:**
- Create: `docs/weather_shadow_schema.sql`
- Create: `tasks/weather_shadow_store.py`
- Create: `tests/test_weather_shadow_store.py`
- Modify: `utils/output_paths.py`

- [ ] Add `WEATHER_SHADOW_DB = DB_STATE_DIR / "weather_shadow.db"` beside the existing canonical DB constants.
- [ ] Write RED test proving `WeatherShadowStore()` performs no filesystem or SQLite I/O and does not create parent directories or journals.
- [ ] Transcribe all columns and checks exactly from the approved spec into five tables: `research_weather_shadow_snapshots`, `research_weather_shadow_quotes`, `research_weather_shadow_outcomes`, `research_weather_shadow_conflicts`, and `research_weather_shadow_outcome_checks`.
- [ ] Add `PRIMARY KEY(snapshot_id)` plus `UNIQUE(capture_key)` for snapshots; `PRIMARY KEY(snapshot_id, market_ticker)` plus a non-cascading snapshot FK for quotes; `PRIMARY KEY(outcome_id)` plus `UNIQUE(market_ticker, source_payload_hash)` for outcomes; and `UNIQUE(event_ticker, check_date_utc, check_kind)` for checks.
- [ ] Add event, market, and check-date indexes. Add `BEFORE UPDATE` and `BEFORE DELETE` triggers raising `ABORT` for every table.
- [ ] Write RED tests proving initialization is idempotent, all constraints/triggers/FKs fire, and every connection applies `PRAGMA foreign_keys=ON`, WAL, and a finite busy timeout.
- [ ] Write RED transaction tests for full snapshot-plus-ladder commit, injected rollback, identical retry, non-identical retry conflict, and two concurrent claimants. First complete `capture_key` wins; a differing loser creates a conflict row and never overwrites the claim.
- [ ] Write RED tests proving partial or empty ladders cannot commit and conflict detail JSON is deterministic and contains no raw credential or response content.
- [ ] Implement only this store API:

```python
class WeatherShadowStore:
    def __init__(
        self,
        db_path: Path = WEATHER_SHADOW_DB,
        schema_path: Path = REPO_ROOT / "docs/weather_shadow_schema.sql",
    ) -> None: ...

    async def initialize(self) -> None: ...
    async def capture_key_state(self, capture_key: str) -> CaptureKeyState: ...
    async def append_capture(self, batch: CaptureBatch) -> CaptureWriteResult: ...
    async def list_outcome_targets(self, now: datetime) -> tuple[OutcomeTarget, ...]: ...
    async def capture_fingerprints(
        self, event_ticker: str
    ) -> dict[str, Fingerprints]: ...
    async def append_outcome_batch(self, batch: OutcomeBatch) -> OutcomeWriteResult: ...
    async def append_outcome_check(self, check: OutcomeCheck) -> CheckWriteResult: ...
    async def try_seal_event(self, event_ticker: str, now: datetime) -> SealResult: ...
    async def label_state(self, event_ticker: str) -> LabelState: ...
```

- [ ] Use `sqlite3` operations behind `asyncio.to_thread`. Each write uses `BEGIN IMMEDIATE`, commits a complete batch, rolls back on every exception, closes its connection unconditionally, and exposes no generic SQL/query API.
- [ ] The caller owns cancellation shielding: a persistence task is always awaited to completion before the orchestration method returns or propagates cancellation.
- [ ] Run `.venv/bin/python -m pytest tests/test_weather_shadow_store.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check tasks/weather_shadow_store.py tests/test_weather_shadow_store.py utils/output_paths.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: add append-only weather shadow store"`.

## Task 3: Add Credential-Free Public Readers

**Files:**
- Create: `kalshi/public_market_data.py`
- Create: `weather/nws_public_client.py`
- Create: `tests/fixtures/weather_shadow/kalshi_events_page_1.json`
- Create: `tests/fixtures/weather_shadow/kalshi_event.json`
- Create: `tests/fixtures/weather_shadow/kalshi_markets_page_1.json`
- Create: `tests/fixtures/weather_shadow/nws_points.json`
- Create: `tests/fixtures/weather_shadow/nws_grid.json`
- Create: `tests/fixtures/weather_shadow/nws_hourly.json`
- Create: `tests/fixtures/weather_shadow/nws_observations.json`
- Create: `tests/fixtures/weather_shadow/nws_cli_products.json`
- Create: `tests/fixtures/weather_shadow/nws_cli_product.json`
- Create: `tests/test_kalshi_public_market_data.py`
- Create: `tests/test_nws_public_client.py`

- [ ] Write RED static-boundary tests proving `kalshi/public_market_data.py` does not import `config`, `kalshi.rest_client`, credentials, signing, trading, executor, account, order, balance, or position code.
- [ ] Write RED HTTP tests proving only GET requests to the fixed public Kalshi market-data origin are possible; reject redirects or returned event/market tickers outside the requested series/event.
- [ ] Write RED pagination tests for the public events/markets APIs, including cursor termination, duplicate ticker rejection, malformed payloads, HTTP failures, and the eight-second timeout.
- [ ] Fix and test the Kalshi contract: origin `https://api.elections.kalshi.com`, API prefix `/trade-api/v2`, `GET /events` with `series_ticker=KXHIGHNY`, `status=open`, `with_nested_markets=true`, `limit=200`, and cursor; `GET /events/{event_ticker}?with_nested_markets=true`; and `GET /markets` with `event_ticker`, `limit=1000`, and cursor. Set `allow_redirects=False`, reject any other origin/path/method, and validate every returned event/market attribution.
- [ ] Implement a narrow protocol and client:

```python
class PublicMarketDataReader(Protocol):
    async def list_active_events(
        self, *, series_ticker: str
    ) -> tuple[RetrievedEvent, ...]: ...
    async def get_event(
        self, *, event_ticker: str
    ) -> RetrievedEvent: ...

class KalshiPublicMarketDataReader:
    # GET event/market data only; no private configuration or auth headers.
    ...
```

- [ ] Enumerate the complete sibling ladder using event nesting or cursor-paginated markets; never accept a prewarm subset as complete.
- [ ] Write RED NWS tests for `/points` discovery, following `forecastGridData` and hourly URLs, KNYC observations, CLI products, explicit User-Agent, timeouts, missing data, unit mismatches, malformed intervals, QC failures, and source/product identity.
- [ ] Fix and test the NWS contract: origin `https://api.weather.gov`; Central Park point `40.7812,-73.9665`; `GET /points/40.7812,-73.9665`; follow only same-origin `forecastGridData` and hourly URLs; `GET /stations/KNYC/observations` with UTC `start`/`end` bounds; `GET /products/types/CLI/locations/NYC`; then `GET /products/{productId}`. Send `User-Agent: kalshi-bot-weather-shadow/1.0 (https://github.com/HushUr2Pups8008/kalshi-bot)`, set `allow_redirects=False`, and reject off-origin discovered URLs.
- [ ] Base parser tests on the committed representative fixture payloads above; keep raw fixtures out of production modules and assert the exact fixture schema keys used by normalization.
- [ ] Implement:

```python
class NwsPublicClient:
    async def fetch_capture_bundle(
        self, *, target_date: date, station_id: str = "KNYC"
    ) -> NwsCapturePayloads: ...
    async def fetch_daily_label(
        self, *, target_date: date, station_id: str = "KNYC"
    ) -> NwsDailyLabel | None: ...
```

- [ ] Return normalized public DTOs and canonical source hashes. Do not derive trading probabilities or persist data in either client.
- [ ] Run `.venv/bin/python -m pytest tests/test_kalshi_public_market_data.py tests/test_nws_public_client.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check kalshi/public_market_data.py weather tests/test_kalshi_public_market_data.py tests/test_nws_public_client.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: add public weather market readers"`.

## Task 4: Orchestrate Bounded Fixed-Horizon Capture

**Files:**
- Create: `tasks/kxhighny_shadow_capture.py`
- Create: `tests/test_kxhighny_shadow_capture.py`

- [ ] Write RED orchestration tests proving an already-claimed capture key skips all network work.
- [ ] Write RED call-order tests proving NWS weather completes before quote retrieval and `weather_retrieved_at <= min(price_retrieved_at)`.
- [ ] Write RED cycle tests for one series fetch, at most two events, concurrency one, 300-second cadence, eight-second request timeouts, and 20-second network/build budget.
- [ ] Write RED tests proving budget expiry cancels network/build work and writes nothing.
- [ ] Write RED tests proving a complete built batch is persisted in a named task under `asyncio.shield`, and cancellation awaits that task before propagating. Assert no background thread or later DB mutation remains after return.
- [ ] Implement:

```python
class WeatherShadowCaptureTask:
    def __init__(
        self,
        *,
        store: WeatherShadowStore,
        capture_markets: PublicMarketDataReader,
        capture_weather: NwsPublicClient,
        label_markets: PublicMarketDataReader,
        label_weather: NwsPublicClient,
        model_version: str = WEATHER_SHADOW_CAPTURE_MODEL_VERSION,
        fee_schedule_version: str = WEATHER_SHADOW_FEE_SCHEDULE_VERSION,
        now: Callable[[], datetime] = utc_now,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None: ...

    async def run(self, stop_event: asyncio.Event | None = None) -> None: ...
    async def run_capture_once(self) -> CaptureCycleResult: ...
    async def capture_event(
        self, event: RetrievedEvent, horizon: HorizonBucket
    ) -> CaptureAttemptResult: ...
```

- [ ] `run()` initializes the store only after the enabled factory creates the task. Capture errors are sanitized and cannot cancel or alter research prewarm.
- [ ] Set `as_of = capture_finished_at`, require the event remains open through completion, and pass only fully enumerated events to pure validation.
- [ ] Add boundary tests monkeypatching research gate, dossier, cache, admission, blend, sizing, paper, executor, and authenticated client functions to raise; capture must still succeed. Use temporary evidence/paper fixtures plus connection spies that fail on canonical production DB paths rather than reading live runtime files.
- [ ] Run `.venv/bin/python -m pytest tests/test_kxhighny_shadow_capture.py tests/test_kxhighny_shadow_validation.py tests/test_weather_shadow_store.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check tasks/kxhighny_shadow_capture.py tests/test_kxhighny_shadow_capture.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: capture bounded KXHIGHNY shadow ladders"`.

## Task 5: Add Atomic Outcomes, Correction Checks, And Sealing

**Files:**
- Modify: `weather/shadow_models.py`
- Modify: `tasks/kxhighny_shadow_validation.py`
- Modify: `tasks/kxhighny_shadow_capture.py`
- Modify: `tasks/weather_shadow_store.py`
- Create: `tests/test_kxhighny_shadow_outcomes.py`

- [ ] Define frozen `OutcomeRow`, `OutcomeBatch`, and `OutcomeCheck` types in `weather/shadow_models.py`, matching every outcome/check column in the approved spec.
- [ ] Write RED validation tests requiring every sibling to be finalized/settled with an explicit yes/no result, unchanged contract/rules/settlement fingerprints, one YES matching the official high, valid NWS CLI identity, and `label_available_at = max(settlement_observed_at, official_retrieved_at)`.
- [ ] Reject an official product issued after its retrieval timestamp or representing a target date other than the captured event's New York civil day.
- [ ] Write RED tests proving stable outcome IDs/hashes exclude quote drift but include every settlement and official-source input.
- [ ] Write RED store tests proving a complete sibling outcome batch is atomic, an injected failure rolls back all rows, identical retries are idempotent, and differing versions are append-only and conflict-quarantined.
- [ ] Write RED correction tests for one restart-safe daily check per UTC day, seven distinct agreeing checks, missing-day quarantine, changed-version quarantine, duplicate checks, and seal creation only after the seventh agreement.
- [ ] Write RED revalidation test proving labels are checked against current public settlement/CLI data again before any future model freeze, holdout, or promotion reader may consume them.
- [ ] Implement pure validation:

```python
def validate_outcome_batch(
    event_payload: RetrievedEvent,
    cli_product: NwsDailyLabel,
    captured_fingerprints: Mapping[str, Fingerprints],
) -> OutcomeBatch: ...

def official_high_market(
    high_f: Decimal,
    quotes: Sequence[ShadowQuote],
) -> str: ...
```

- [ ] Implement `run_label_once()` and `label_event()` as an independent failure lane using the dedicated `label_markets` and `label_weather` instances. Tests prove capture and labeling have separate call histories, timeouts, and budgets and cannot starve each other.
- [ ] Query only captured events lacking a complete label or requiring a daily check inside the seven-day window. Never use paper settlement state.
- [ ] Run `.venv/bin/python -m pytest tests/test_kxhighny_shadow_outcomes.py tests/test_weather_shadow_store.py tests/test_kxhighny_shadow_capture.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check tasks/kxhighny_shadow_validation.py tasks/kxhighny_shadow_capture.py tasks/weather_shadow_store.py tests/test_kxhighny_shadow_outcomes.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: label and seal weather shadow outcomes"`.

## Task 6: Wire A Truly Lazy Disabled-Default Runtime

**Files:**
- Modify: `config.py`
- Modify: `.env.example`
- Modify: `main.py`
- Create: `tests/test_main_weather_shadow.py`
- Modify: `tests/test_main_pipeline.py`

- [ ] Write RED config tests proving absent `ENABLE_WEATHER_SHADOW_CAPTURE` is false and existing accepted boolean spellings parse through `_parse_bool_env`.
- [ ] Add `BotConfig.enable_weather_shadow_capture` with `default_factory=lambda: _parse_bool_env("ENABLE_WEATHER_SHADOW_CAPTURE", "false")` and document `false` in `.env.example`.
- [ ] Write RED disabled-path test that clears weather modules from `sys.modules`, calls the factory, and proves no import, object, DB path, SQLite connection, DDL, network client, or task occurs.
- [ ] Add a synchronous `_create_weather_shadow_runtime_task()` beside `_create_research_prewarm_runtime_task()` with all weather imports inside the enabled branch.
- [ ] On enablement, construct exactly one named `weather_shadow_capture` supervisor using `WeatherShadowStore` plus separate `capture_markets`, `capture_weather`, `label_markets`, and `label_weather` public client instances.
- [ ] Append the returned task only when non-null. Do not add weather attributes or imports to `TradingBot.__init__`.
- [ ] Keep the existing global runtime-task shutdown behavior unchanged. Retain the weather task reference separately; after cancelling tasks, consume only weather completion with `await asyncio.gather(weather_shadow_task, return_exceptions=True)` so its re-propagated `CancelledError` cannot skip existing cleanup. Put the drain in a nested `try/finally` that always runs `cancel_targeted_research_prewarm_tasks()` and `ws.stop()`. SQLite busy timeout and the short transaction bound the drain without awaiting unrelated tasks.
- [ ] Write RED enabled/cancellation tests proving exactly one task is created, main awaits its persistence drain, existing targeted-prewarm and websocket cleanup still run after cancellation, and an unrelated cancellation-resistant runtime task is not newly awaited by this feature.
- [ ] Run `.venv/bin/python -m pytest tests/test_main_weather_shadow.py tests/test_main_pipeline.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check config.py main.py tests/test_main_weather_shadow.py`; expect PASS.
- [ ] Commit: `git commit -m "feat: wire disabled weather shadow runtime"`.

## Task 7: Add Health And Optional Backup Coverage

**Files:**
- Modify: `scripts/botcheck.py`
- Modify: `tests/test_botcheck.py`
- Modify: `scripts/db_snapshot_backup.sh`
- Create or modify: `tests/test_db_snapshot_backup.py`

- [ ] Write RED botcheck tests proving disabled status does not open/create the DB and enabled status reads only `weather_shadow.db` health, table presence, and bounded row counts.
- [ ] Add a concise `weather_shadow` status line showing flag source and, only when enabled and present, integrity/last-capture status.
- [ ] Write RED backup tests proving a missing weather DB is skipped without failure or creation.
- [ ] Add a dormant `--include-weather` option. Only that explicit option enables the `data/weather_shadow.db` `.backup` branch; default invocations preserve the existing two-DB behavior exactly. When opted in and present, restore to a temporary DB and require `PRAGMA integrity_check = ok` before reporting success.
- [ ] Preserve the existing mandatory paper/evidence snapshot and validation behavior unchanged. Write RED tests proving the default does not inspect weather state; opt-in handling never mutates any source DB, skips an absent weather DB without creating it, and reports present weather snapshot size separately.
- [ ] Run `.venv/bin/python -m pytest tests/test_botcheck.py tests/test_db_snapshot_backup.py -q`; expect PASS.
- [ ] Run `.venv/bin/ruff check scripts/botcheck.py tests/test_botcheck.py tests/test_db_snapshot_backup.py`; expect PASS.
- [ ] Run `bash -n scripts/db_snapshot_backup.sh`; expect PASS.
- [ ] Commit: `git commit -m "chore: monitor and back up weather shadow data"`.

## Task 8: Independent Review And Disabled Protected Rollout

**Files:**
- Review all weather-shadow paths; do not mutate the LaunchAgent flag in this task.

- [ ] Ask an independent reviewer to verify disabled-path zero-I/O behavior, credential/order isolation, complete-ladder semantics, timing causality, hash coverage, append-only enforcement, cancellation safety, outcome correction logic, and production-DB isolation.
- [ ] Fix every accepted finding with a RED regression first, then rerun focused tests.
- [ ] Run `make lint`; expect PASS.
- [ ] Run `scripts/run_tests.sh`; expect PASS with no failures.
- [ ] Run a temporary-directory integration probe that initializes the separate DB, captures a fixture ladder, labels it, performs seven checks, and validates all five tables plus `PRAGMA integrity_check`.
- [ ] Inspect `git diff --check` and `git status --short`; confirm only intended code/tests/docs are staged and runtime artifacts remain unstaged.
- [ ] Merge the circuit PR first because both features touch `config.py`, `.env.example`, botcheck, and botcheck tests. Rebase the dedicated weather branch onto updated `main`, resolve only those shared surfaces, rerun combined config/botcheck/full suites, then open the protected weather-shadow PR.
- [ ] Wait for required CI. Use the user's approved override only if repository policy/check state requires it and report the exact overridden check.
- [ ] Merge under the user's recorded authorization, sync local `main`, restart through the existing protected launchd workflow, and run `.venv/bin/python scripts/botcheck.py`.
- [ ] Prove the deployed flag is absent/false, the bot is healthy, and `data/weather_shadow.db` was not created or changed by restart.
- [ ] Stop. Do not enable capture during this rollout.

## Task 9: Separate Operator-Gated Enablement And Rollback

**Files:**
- External runtime configuration only after explicit operator approval; no code commit.

- [ ] Present the disabled rollout evidence and request a separate explicit enablement decision.
- [ ] If approved, use the exact enable/restart/health sequence:

```bash
PLIST="$HOME/Library/LaunchAgents/com.jake.kalshi-bot.plist"
/usr/libexec/PlistBuddy -c \
  "Set :EnvironmentVariables:ENABLE_WEATHER_SHADOW_CAPTURE true" "$PLIST" || \
/usr/libexec/PlistBuddy -c \
  "Add :EnvironmentVariables:ENABLE_WEATHER_SHADOW_CAPTURE string true" "$PLIST"
plutil -lint "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl print "gui/$(id -u)/com.jake.kalshi-bot" | rg "state =|pid ="
.venv/bin/python scripts/botcheck.py
```

- [ ] After at least 310 seconds, inspect only through read-only SQLite connections; require integrity `ok`, exactly five weather application tables, bounded row growth, unique fixed-horizon claims, and zero weather table names in evidence/paper stores:

```bash
sqlite3 -readonly data/weather_shadow.db \
  "PRAGMA integrity_check; SELECT name FROM sqlite_schema WHERE type='table' AND name LIKE 'research_weather_shadow_%' ORDER BY name; SELECT horizon_bucket, COUNT(*) FROM research_weather_shadow_snapshots GROUP BY horizon_bucket;"
sqlite3 -readonly data/evidence_store.db \
  "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'research_weather_shadow_%';"
sqlite3 -readonly data/paper_trades.db \
  "SELECT COUNT(*) FROM sqlite_schema WHERE name LIKE 'research_weather_shadow_%';"
```

- [ ] After live capture enablement is proven, register the weather DB by using the explicit opt-in in the backup service configuration under operator control. Run the backup and exact restore-integrity sequence:

```bash
bash scripts/db_snapshot_backup.sh --include-weather
LATEST_WEATHER="$(find logs/backups/db_snapshots -type f -name weather_shadow.db -print0 | xargs -0 ls -t | head -1)"
test -n "$LATEST_WEATHER"
RESTORE_DB="$(mktemp -t weather-shadow-restore)"
trap 'rm -f "$RESTORE_DB"' EXIT
sqlite3 "$LATEST_WEATHER" ".backup '$RESTORE_DB'"
test "$(sqlite3 -readonly "$RESTORE_DB" 'PRAGMA integrity_check;')" = "ok"
rm -f "$RESTORE_DB"
trap - EXIT
```
- [ ] Continue shadow collection only. Promotion requires at least 60 event-days, 20 untouched holdout days, executable-fee-aware EV, calibration/discrimination reporting, leakage audit, label revalidation, and a separate reviewed design.
- [ ] Rollback may record a pre-disable count for attribution, then sets the flag false with `PlistBuddy`, lints and restarts with the same `bootout`/`bootstrap` sequence, and proves false/healthy after shutdown drain. Record the authoritative five-table baseline only after that restart; wait at least 310 seconds and prove those post-drain counts are unchanged while leaving the append-only database intact.
