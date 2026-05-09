# Security Audit — 2026-05-08

Scope: kalshi/, governance/, feeds/, analysis/, tasks/, utils/, ops/, scripts/ (*.py),
root main.py + config.py, .env.example, .gitignore, .gitlab-ci.yml, .githooks/pre-commit.
Excluded: /trading, /tests, /mac_archive, /windows_archive, /data, /logs.

---

## Verified Load-Bearing Patterns

### 1. RSA-PSS / SHA-256 with salt_length=DIGEST_LENGTH

**Confirmed in both clients.**

- `kalshi/rest_client.py:89–96` — `private_key.sign(message, asym_padding.PSS(mgf=asym_padding.MGF1(hashes.SHA256()), salt_length=asym_padding.PSS.DIGEST_LENGTH), hashes.SHA256())`
- `kalshi/websocket_client.py:87–94` — identical call pattern in `_build_ws_auth_headers()`

No drift detected.

### 2. `_normalize_pem()` present in both clients

**Confirmed.**

- `kalshi/rest_client.py:28–41` — `_normalize_pem()` replaces literal `\n` → real newlines, adds headers if absent.
- `kalshi/websocket_client.py:51–64` — identical implementation.

Both are called before `load_pem_private_key()` at their respective sign paths.

### 3. websockets version-detect for `extra_headers` vs `additional_headers`

**Confirmed.**

- `kalshi/websocket_client.py:26–28`:
  ```python
  _ws_ver = tuple(int(x) for x in websockets.__version__.split(".")[:2])
  _WS_HEADER_KWARG = "additional_headers" if _ws_ver >= (14, 0) else "extra_headers"
  ```
  Used at `websocket_client.py:228` via `**{_WS_HEADER_KWARG: auth_headers}`.

No drift detected.

### 4. `LocalQwenLLM.complete` passes top-level `think: False`

**Confirmed.**

- `governance/llm.py:205–211` — `think: False` is a sibling of `format` and `stream` in the payload dict, not nested under `options`. Comment at lines 196–204 documents the PROFIT-GOV-001 rationale.

No drift detected.

---

## P0 — Critical

No P0 findings.

---

## P1 — High

| File:Line | Type | Description | Remediation |
|-----------|------|-------------|-------------|
| `kalshi/rest_client.py:98–100` | Silent auth bypass on signing failure | When `_sign()` catches any exception (bad PEM, missing cryptography dep, transient error), it logs a warning and returns a headers dict containing only `KALSHI-ACCESS-TIMESTAMP` — no key ID, no signature. `_headers()` then includes these incomplete headers and the request is sent unsigned. For trade placement, the request will be rejected by Kalshi as unauthorized, but the bot's error path treats this as a network error rather than a fatal auth failure, masking a misconfigured credential. | Re-raise the signing exception (or have `_sign` return `None` and have `_headers` refuse to build auth headers) so the request is aborted rather than sent unsigned. A misconfigured PEM should surface as a startup failure, not a silent per-request degradation. |
| `kalshi/websocket_client.py:96–98` | Silent auth bypass on WS signing failure | `_build_ws_auth_headers()` catches all exceptions and returns `{}`. The WebSocket then connects without any auth headers. The connection may succeed (Kalshi may allow unauthed WS for public feeds) but price updates for any authenticated scope would be silently unavailable. | Same remediation: propagate the exception so the connect attempt fails explicitly rather than silently opening an unauthenticated channel. |

---

## P2 — Medium

| File:Line | Type | Description | Remediation |
|-----------|------|-------------|-------------|
| `governance/llm.py:185` | Unvalidated `base_url` constructor argument | `LocalQwenLLM.__init__` accepts `base_url` with no scheme or host validation. `urllib.request.urlopen` will follow any URL passed. In current call paths this value comes from `os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")` (via config), but the constructor itself imposes no restriction — a future caller could pass an external URL. | Validate that `base_url` matches `http://localhost:*` or `http://127.0.0.1:*` before storing it. Reject non-localhost values at construction time. |
| `config.py:303–304` | Unvalidated scheme in `FADE_TWEET_FEED_URLS` | `FADE_TWEET_FEED_URLS` is read from the environment, comma-split, and passed directly to `feedparser.parse()`. No scheme validation occurs. `feedparser` supports `file://`, `ftp://`, and other schemes in addition to HTTP. A misconfigured or attacker-influenced `.env` value could cause the RSS monitor to read arbitrary local files or internal network resources. | Filter the parsed URL list to `https://` scheme only before storing. Log and skip any entry that does not pass. |
| `ops/launchd/*.plist.template` | No `.env` existence check at launchd start | The plist `EnvironmentVariables` block contains only `PYTHONUNBUFFERED`. All credentials are loaded by `config.py` via `load_dotenv`. If `.env` is absent at launchd start time, config validation prints `[CONFIG ERROR]` to stderr but the process continues in a degraded/unsigned state. | Add a pre-flight check to `install.sh` or the plist `ProgramArguments` that verifies `.env` exists and is non-empty before launching the bot process. |

---

## P3 — Low

| File:Line | Type | Description | Remediation |
|-----------|------|-------------|-------------|
| `.gitignore:75–76` | Redundant specific PAT filename alongside glob | `.gitignore` has `*_pat.txt` (glob, line 75) and `JRP_GamingDesktop_pat.txt` (specific, line 76). The specific entry is redundant with the glob. If the glob is removed during a future cleanup, the specific name would remain protected but other `*_pat.txt` files would not. | Remove the redundant specific entry; the glob is sufficient. |
| `kalshi/rest_client.py:149–157` | Error-body redaction is best-effort keyword scan | The `HTTPError` handler scans the response body for credential-adjacent strings and replaces it if found. This is sound but misses novel field names or compound responses. It also applies only to `HTTPError`; `RequestException` logs `exc` directly. | Acceptable as-is. Consider capping the logged body at a fixed length (e.g., 200 chars) unconditionally before the redaction check, so the redaction is a defense-in-depth layer rather than the only truncation. |
| `scripts/test_coverage_audit.py:47–61` | subprocess with internal `cmd` list | `subprocess.run(cmd, ...)` where `cmd` is constructed from constants and `pytest_args` which is internally sourced. No `shell=True`. Not a current risk. | Add a comment that `pytest_args` must not accept user-supplied input if the script is ever exposed as a CLI tool. |
| `.gitlab-ci.yml:24` | Docker image not pinned to digest | `image: python:3.14-slim` uses a floating tag. If the upstream image is compromised or changes behavior, CI picks it up on the next run. | Pin to a digest (`python:3.14-slim@sha256:...`) for reproducibility. Acceptable risk for a solo project but worth tracking. |

---

## Configuration / CI / Secrets Hygiene

### .gitignore

- `.env` is listed at line 1. Clean.
- SQLite WAL/SHM/journal files ignored. Clean.
- Log directories ignored via glob. Clean.
- `*_pat.txt` glob covers PAT files. See P3 above.
- `WINDOWS_COMMANDS.md` and `setup_service.ps1` are ignored — appropriate for machine-specific files that may contain paths.
- Missing: `.env.*` variants (e.g., `.env.local`, `.env.production`, `.env.test`) are not listed. Any such file created by accident would not be protected. Recommend adding `.env.*` excluding `.env.example` to the ignore file.

### .env.example

- All credential fields use clearly placeholder values (`your-api-key-uuid-here`, `YOUR_BASE64_KEY_BODY_HERE`, `sk-ant-your-key-here`). No real secrets present.
- `KALSHI_ENV=demo` default is correct — paper-first is safe.
- `LIVE_TRADING_ENABLED=false` default is correct.
- All LLM and Reddit secret keys are commented out with instructions. Clean.
- `BANKROLL=50.00` is active (not commented). This is intentional and safe (errs conservative), and the file documents it clearly.

### .gitlab-ci.yml

- No secrets, tokens, or API keys in the CI config. Clean.
- `variables:` block contains only `PIP_CACHE_DIR`, `PYTHONUNBUFFERED`, `PIP_USE_PEP517`. Clean.
- CI does not execute `git config core.hooksPath .githooks` — the pre-commit hook is inert in CI. The README/VERSION sync safety net is the `sync_readme_version.py --check` call in the lint job. Design is sound.
- No force-push, history rewrite, or branch modification steps. Clean.

### .githooks/pre-commit

- Invokes `$VENV_PYTHON` (repo-relative path). If `.venv` is absent the hook exits with a non-zero error, not a silent skip. Safe.
- Only modifies `README.md` after staging `VERSION`. Does not read or stage credential files.
- No shell injection vectors: all variables are single-quoted or derived from `git rev-parse`. Clean.

---

## Repo-wide Observations

- All outbound API calls use HTTPS/WSS. Kalshi REST/WS base URLs are `https://` and `wss://`. The `http://` string in `kalshi/websocket_client.py:80` is a transient local transformation used only to extract the URL path component for the signing message; it is never used as a connection target. GDELT uses `https://api.gdeltproject.org`. Reddit OAuth uses `https://www.reddit.com`. Ollama defaults to `http://localhost` (local-only). No insecure external transport found.

- No hardcoded credentials anywhere in scope. All secrets load exclusively via `os.getenv()` in `config.py:BotConfig`, backed by `load_dotenv()`. No fallback values contain real secrets. The `.env` file itself is `.gitignore`d and has no git history.

- No unsafe deserialization, eval, exec, or shell injection found. All `subprocess` calls in scripts use list-form arguments without `shell=True`. No `pickle`, `marshal`, `yaml.unsafe_load`, or `eval()` usage was found in scope.

- SQL parameterization is consistent throughout. All `sqlite3.execute()` calls reviewed use `?` placeholders. No f-string or `%`-format SQL interpolation was found.

- The silent-auth-bypass pattern (P1) in both Kalshi clients is the single highest-priority finding. A PEM misconfiguration or transient crypto failure will not prevent the bot from sending requests; it will send them unsigned, masking the root cause behind HTTP 401 errors that look like network failures.
