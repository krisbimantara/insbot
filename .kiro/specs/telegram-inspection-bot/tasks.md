# Implementation Plan: Telegram Inspection Bot

## Overview

Implementasi Telegram Inspection Bot sebagai middleware Python async (aiogram 3.x + aiohttp) yang menjembatani Frappe ERPNext dengan inspektor lapangan melalui Telegram. Bot memandu inspektor menyelesaikan checklist 66 komponen wajib + conditional STNK + 10 foto wajib, lalu mengirim hasil ke Frappe via REST API. Arsitektur menggunakan domain layer pure (testable) yang terpisah dari adapter I/O, dengan session disimpan di Redis.

## Tasks

- [x] 1. Set up project structure, configuration, and core data models
  - [x] 1.1 Create project directory structure and dependency files
    - Create `pyproject.toml` with dependencies: aiogram ^3.4, aiohttp ^3.9, redis[hiredis] ^5.0, Pillow ^10.x, pydantic-settings ^2.x, structlog ^24.x, cachetools ^5.x, hypothesis ^6.x, pytest, pytest-asyncio
    - Create directory structure: `src/bot/`, `src/bot/domain/`, `src/bot/adapters/`, `src/bot/handlers/`, `tests/`, `tests/properties/`, `tests/unit/`, `tests/integration/`
    - Create `src/bot/__init__.py` and all sub-package `__init__.py` files
    - _Requirements: 12.1_

  - [x] 1.2 Implement configuration module (`src/bot/config.py`)
    - Define `Settings(BaseSettings)` class with all fields: `frappe_url`, `frappe_api_key`, `frappe_api_secret`, `telegram_bot_token`, `redis_url`, `redis_ttl`, `webhook_host`, `webhook_port`, `webhook_shared_secret`, `auth_cache_ttl_seconds`, `frappe_request_timeout_seconds`, `photo_max_bytes`, `photo_compress_target_longest_edge`, `log_level`
    - Implement `model_post_init` to check HTTPS scheme and log warning if non-HTTPS
    - Ensure fail-fast on missing required env vars with descriptive error (no secret values in message)
    - _Requirements: 12.1, 12.2, 12.3, 12.5_

  - [x] 1.3 Implement core data models and constants (`src/bot/domain/models.py`)
    - Define `Phase` enum: IDLE, SELECTED, CHECKLIST, STNK_CONDITIONAL, PHOTOS, SUMMARY, REVISION
    - Define `CATEGORIES` tuple (8 categories in fixed order)
    - Define `MANDATORY_FIELDS` tuple (66 fields in fixed order per Requirement 14.1)
    - Define `COMPONENT_OPTIONS` dict (default Baik/Cukup/Rusak; bahan_bakar → E/1/4/1/2/3/4/F)
    - Define `PHOTO_FIELDS` tuple (10 photo names in fixed order)
    - Define `STNK_CONDITIONAL_BY_ANSWER` dict
    - Define Pydantic models: `MotorTarikan`, `MotorMeta`, `CategoryProgress`, `Question`, `ValidationError`, `Session`, `SubmitPayload`, `SubmitResult`
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 9.3_

  - [x] 1.4 Implement structured logging setup (`src/bot/logging.py`)
    - Configure `structlog` with JSON output to STDOUT
    - Create helper functions for audit events (INSPECTION_REQUESTED, INSPECTION_STARTED, CATEGORY_REVISED, SUBMIT_SUCCESS, SUBMIT_FAILED)
    - Ensure no secret values are logged
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 12.3_

  - [ ]* 1.5 Write property test for Session serialization round-trip
    - **Property 1: Session Serialization Round-Trip**
    - Test that for any valid Session instance, `model_dump_json()` then `model_validate_json()` produces an equal object
    - Create custom Hypothesis strategy `arbitrary_session` in `tests/strategies.py`
    - **Validates: Requirements 9.1, 9.3, 9.4**

- [x] 2. Implement domain layer — pure functions
  - [x] 2.1 Implement checklist and FSM logic (`src/bot/domain/checklist.py`, `src/bot/domain/fsm.py`)
    - Implement `next_question(session) -> Question | Done` — returns next question based on session state
    - Implement `apply_answer(session, field, value) -> Session` — writes answer, recalculates progress, advances pointer
    - Implement FSM transition logic matching the state diagram (IDLE → SELECTED → CHECKLIST → STNK_CONDITIONAL → PHOTOS → SUMMARY → REVISION)
    - Implement `determine_keyboard_type(session) -> Literal["reply", "inline", "remove"]`
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.9_

  - [ ]* 2.2 Write property test for answer validation
    - **Property 9: Answer Validation**
    - Test that valid options are accepted and advance pointer; invalid options are rejected with no state change
    - **Validates: Requirements 4.4, 4.5, 4.8, 16.6**

  - [ ]* 2.3 Write property test for next question ordering
    - **Property 10: Next Question Ordering**
    - Test that `next_question` returns fields in deterministic order matching MANDATORY_FIELDS
    - **Validates: Requirements 4.1, 4.2**

  - [x] 2.4 Implement conditional STNK logic (`src/bot/domain/stnk.py`)
    - Implement `stnk_relevant_fields(stnk_value) -> tuple[str, ...]`
    - Implement `prune_irrelevant_stnk(answers, stnk_value) -> dict`
    - Implement conditional question flow (Ya/Tidak for boolean fields, date validation for stnk_mati_tanggal)
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [ ]* 2.5 Write property test for conditional STNK invariant
    - **Property 3: Conditional STNK Invariant**
    - Test that `stnk_relevant_fields` returns correct fields per value, and `prune_irrelevant_stnk` removes only irrelevant conditional keys
    - **Validates: Requirements 5.1, 5.2, 5.3, 5.6, 5.7**

  - [x] 2.6 Implement payload builder (`src/bot/domain/payload.py`)
    - Implement `build_submit_payload(session, foto_urls) -> SubmitPayload`
    - Implement `build_idempotency_key(session) -> str` — format: `{telegram_id}:{motor_tarikan}:{session_started_at}`
    - Ensure `komponen` contains exactly 66 mandatory keys + conditional STNK non-null fields
    - _Requirements: 14.1, 14.2, 14.3, 14.4, 8.4, 8.7_

  - [ ]* 2.7 Write property test for payload completeness
    - **Property 2: Payload Completeness and Validity Invariant**
    - Test that for any session passing `validate_pre_submit`, the payload has exactly 66 mandatory keys with valid values, correct conditional STNK inclusion, and 10 photo URLs
    - **Validates: Requirements 14.1, 14.2, 14.3, 14.4, 8.4**

  - [ ]* 2.8 Write property test for idempotency key determinism
    - **Property 11: Idempotency Key Determinism**
    - Test that `build_idempotency_key` returns identical strings for sessions with same telegram_id, motor_id, started_at regardless of other field differences
    - **Validates: Requirement 8.7**

  - [x] 2.9 Implement pre-submit validation (`src/bot/domain/validation.py`)
    - Implement `validate_pre_submit(session) -> list[ValidationError]`
    - Check all 66 mandatory fields have valid values from their option sets
    - Check all 10 photos have file_id
    - _Requirements: 8.1, 8.2_

  - [x] 2.10 Implement progress computation (`src/bot/domain/progress.py`)
    - Implement `compute_progress(session) -> tuple[CategoryProgress, ...]`
    - Implement `render_progress_bar(done, total, width=10) -> str`
    - _Requirements: 4.3_

  - [ ]* 2.11 Write property test for progress monotonicity
    - **Property 6: Progress Monotonicity**
    - Test that `done` count is non-decreasing in inspeksi mode; in revisi mode `done` never exceeds `total`
    - **Validates: Requirements 4.7, 7.6**

  - [ ]* 2.12 Write property test for revision confluence
    - **Property 5: Revision Confluence**
    - Test that two different orderings of category revisions producing the same final answers yield identical payload `komponen`
    - **Validates: Requirement 7**

  - [ ]* 2.13 Write property test for revision answer semantics
    - **Property 13: Revision Answer Semantics**
    - Test that Skip preserves old value, new valid value overwrites, and revision_history is updated
    - **Validates: Requirements 7.4, 7.5, 7.6**

  - [ ]* 2.14 Write property test for cancel guard condition
    - **Property 14: Cancel Guard Condition**
    - Test that `/batal` is rejected when `inspection_started=True` and clears motor selection when `inspection_started=False`
    - **Validates: Requirements 10.5, 10.6**

  - [ ]* 2.15 Write property test for keyboard type invariant
    - **Property 8: Keyboard Type Invariant per Phase**
    - Test that CHECKLIST/STNK_CONDITIONAL/revisi phases use Reply Keyboard; IDLE/SELECTED/PHOTOS/SUMMARY use Inline Keyboard; transitions include ReplyKeyboardRemove
    - **Validates: Requirements 16.1, 16.2, 16.4, 16.5, 4.10**

- [x] 3. Checkpoint — Ensure domain layer tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 4. Implement adapters (I/O layer)
  - [x] 4.1 Implement Redis session store (`src/bot/adapters/redis_store.py`)
    - Implement `RedisSessionStore` class with methods: `get_session`, `save_session`, `delete_session`, `add_pending`, `remove_pending`, `replace_pending`, `list_pending`, `ping`
    - Use key format `session:{telegram_id}:{motor_id}` with TTL 86400s refreshed on every save
    - Use key format `pending:{telegram_id}` as Redis SET with TTL 86400s
    - Serialize Session via `model_dump_json()` / `model_validate_json()`
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.7_

  - [ ]* 4.2 Write property test for webhook idempotency
    - **Property 4: Webhook Idempotency**
    - Test that processing the same webhook payload N times results in identical pending_motors set as processing it once (using mock Redis)
    - **Validates: Requirements 1.2, 1.8**

  - [ ]* 4.3 Write property test for pending sync invariant
    - **Property 7: Pending Sync Invariant**
    - Test that after `replace_pending`, the resulting set equals exactly the Frappe response motor IDs
    - **Validates: Requirements 3.7, 15.1**

  - [x] 4.4 Implement Frappe HTTP client (`src/bot/adapters/frappe.py`)
    - Implement `FrappeClient` class with methods: `get_pending_list`, `upload_foto`, `submit_hasil_inspeksi`
    - Set Authorization header `token {key}:{secret}` on every request
    - Set request timeout to 30s
    - Map HTTP errors to exception hierarchy: `FrappePermissionError`, `FrappeNotFound`, `FrappeValidationError`, `FrappeUnavailable`
    - Implement `FrappeValidationError.indicates_already_completed()` and `indicates_payload_incomplete()`
    - _Requirements: 8.3, 8.4, 8.7, 8.8, 8.9, 11.4, 12.4, 15.3_

  - [x] 4.5 Implement photo manager (`src/bot/adapters/photos.py`)
    - Implement `download_telegram_photo(file_id) -> bytes`
    - Implement `compress_if_needed(image_bytes, max_bytes, longest_edge) -> bytes` — downscale + JPEG quality stepping (90→75→60)
    - _Requirements: 6.8_

  - [ ]* 4.6 Write property test for photo compression bound
    - **Property 12: Photo Compression Bound**
    - Test that output is always ≤ max_bytes for oversized input, and identity for undersized input
    - **Validates: Requirement 6.8**

  - [x] 4.7 Implement exception hierarchy (`src/bot/adapters/exceptions.py`)
    - Define all exception classes: `InspectionBotError`, `FrappeError`, `FrappePermissionError`, `FrappeNotFound`, `FrappeValidationError`, `FrappeUnavailable`, `SessionError`, `SessionExpired`, `SessionNotFound`, `PreSubmitValidationError`, `StatusChanged`, `StatusMismatch`
    - _Requirements: 8.8, 8.9, 8.10, 9.5, 9.6, 15.2, 15.3_

- [x] 5. Checkpoint — Ensure adapter tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implement webhook server and auth middleware
  - [x] 6.1 Implement webhook server (`src/bot/webhook.py`)
    - Create aiohttp app with `POST /webhook/inspection-request` endpoint
    - Validate shared secret header (`X-Inspection-Webhook-Secret`)
    - Validate payload: check `event == "inspection_requested"`, required fields present
    - On valid: add to pending via Redis, send Telegram notification, return 200
    - On invalid: return 400 with descriptive body
    - Implement `GET /healthz` endpoint (200 if Redis PING ok, 503 otherwise)
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 11.5, 12.6_

  - [x] 6.2 Implement auth middleware (`src/bot/auth_middleware.py`)
    - Implement `FrappeAuthMiddleware(BaseMiddleware)` with TTLCache (60s)
    - Call `get_pending_list` on cache miss to validate telegram_id
    - Handle FrappePermissionError → "Akses ditolak. Hubungi admin."
    - Handle FrappeUnavailable → "Sistem sedang sibuk, silakan coba lagi sebentar."
    - Webhook requests bypass this middleware
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 2.7_

  - [ ]* 6.3 Write unit tests for webhook handler and auth middleware
    - Test valid webhook processing (200 response, Redis updated, Telegram notified)
    - Test invalid event (400), missing fields (400), failed Telegram send (still 200)
    - Test auth middleware: authorized, unauthorized, Frappe unavailable, cache hit
    - _Requirements: 1.1–1.8, 2.1–2.7_

- [x] 7. Implement Telegram handlers — commands and motor selection
  - [x] 7.1 Implement command handlers (`src/bot/handlers/commands.py`)
    - `/start` — welcome message + Inline Keyboard `[Lihat Daftar Motor]`
    - `/mulai` — trigger motor list flow
    - `/bantuan` — help text with available commands
    - `/status` — show pending count, active motor, current category, completion percentage
    - `/batal` — only when `inspection_started=false`; reject with message when true
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6_

  - [x] 7.2 Implement motor selection handler (`src/bot/handlers/motor_selection.py`)
    - Display pending motors as Inline Keyboard buttons (format: `{merk} {model} {tahun} — {nopol}`)
    - Handle motor tap: create/load session, show confirmation card with `[Mulai Inspeksi]`
    - Handle existing active session: show `[Lanjutkan Sesi Sebelumnya]` and `[Mulai Ulang]`
    - Refresh `pending_motors` from Frappe on every list display
    - Set `tipe_inspeksi` based on `status_inspeksi`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8_

  - [ ]* 7.3 Write unit tests for command and motor selection handlers
    - Test `/start`, `/mulai`, `/bantuan`, `/status`, `/batal` responses
    - Test motor list display, selection, resume/restart flow
    - Test empty pending list message
    - _Requirements: 10.1–10.6, 3.1–3.8_

- [x] 8. Implement Telegram handlers — checklist, STNK, and photos
  - [x] 8.1 Implement checklist handler (`src/bot/handlers/checklist.py`)
    - Display component questions one by one with Reply Keyboard (Baik/Cukup/Rusak or fuel options)
    - Validate answer against valid option set; re-display on invalid
    - Save answer to Redis before advancing; handle Redis failure
    - Show progress bar `[████████░░] {done}/{total}`
    - Handle category transitions and completion
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 4.10_

  - [x] 8.2 Implement STNK conditional handler (`src/bot/handlers/stnk.py`)
    - Display conditional questions based on stnk answer (Cukup: 3 questions, Rusak: 4 questions)
    - Ya/Tidak/Skip Reply Keyboard for boolean fields
    - Date input with validation (YYYY-MM-DD regex) for `stnk_mati_tanggal`
    - Skip saves null and advances
    - Store `stnk_answer` separately in session
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8_

  - [x] 8.3 Implement photo capture handler (`src/bot/handlers/photos.py`)
    - Display photo prompts in fixed order with label, description, and progress `Foto {N}/10`
    - Send `ReplyKeyboardRemove` on first photo prompt
    - Accept photo/document-image, save file_id; reject non-image with error message
    - Show Inline Keyboard `[Konfirmasi]` `[Foto Ulang]` after each photo
    - Handle confirm (advance index) and retry (clear and re-prompt)
    - Transition to Summary when all 10 confirmed
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.9_

  - [ ]* 8.4 Write unit tests for checklist, STNK, and photo handlers
    - Test valid/invalid answer handling, progress display, category transitions
    - Test STNK conditional branching (Baik skips, Cukup shows 3, Rusak shows 4)
    - Test photo accept/reject, confirm/retry flow
    - _Requirements: 4.1–4.10, 5.1–5.8, 6.1–6.9_

- [x] 9. Implement Telegram handlers — summary, revision, and submit
  - [x] 9.1 Implement summary and revision handler (`src/bot/handlers/summary.py`)
    - Display summary page: motor name, 8 categories with done/total and (Direvisi) marker, photo status, Inline Keyboard `[Revisi Kategori]` `[Kirim Hasil]`
    - Handle `Revisi Kategori` tap: show 8 categories as Inline Keyboard
    - Handle category selection: set mode=revisi, re-display category components with old answers + Reply Keyboard (options + Skip)
    - Handle Skip (preserve old value) and new answer (overwrite)
    - On revision complete: update revision_history, set mode=ringkasan, apply STNK prune if category 8 revised, show summary with ReplyKeyboardRemove
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 7.7, 7.8_

  - [x] 9.2 Implement submit handler (`src/bot/handlers/submit.py`)
    - Validate pre-submit (66 fields + 10 photos); show missing fields on failure
    - Refresh tipe_inspeksi check against Frappe before submit
    - Download and compress 10 photos serially, upload to Frappe
    - Build payload via `build_submit_payload` and idempotency key
    - Submit with retry (3× exponential backoff 2s/4s/8s)
    - Handle success: delete session, remove from pending, show confirmation + optional `[Lihat Daftar Motor]`
    - Handle errors: ValidationError (payload incomplete → back to summary), already completed → treat as success, 5xx → retry then manual retry message, 403 → access denied + delete session
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7, 8.8, 8.9, 8.10, 8.11, 14.5, 15.2, 15.3_

  - [ ]* 9.3 Write unit tests for summary, revision, and submit handlers
    - Test summary display, revision flow, STNK prune on revision
    - Test submit pipeline: success, validation failure, already completed, network error with retry
    - Test reassignment detection
    - _Requirements: 7.1–7.8, 8.1–8.11_

- [x] 10. Checkpoint — Ensure all handler tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 11. Wire components together and implement bot runner
  - [x] 11.1 Implement bot runner and application wiring (`src/bot/bot_runner.py`)
    - Initialize Settings, structlog, Redis connection, FrappeClient, RedisSessionStore
    - Create aiogram Bot and Dispatcher
    - Register auth middleware on dispatcher
    - Register all handlers (commands, motor selection, checklist, STNK, photos, summary, submit)
    - Start aiohttp app (webhook server + healthz) alongside aiogram polling
    - Implement graceful shutdown
    - _Requirements: 11.3, 11.5, 12.1, 12.2_

  - [x] 11.2 Create Dockerfile and docker-compose configuration
    - Single-container Dockerfile (Python 3.11 slim base)
    - docker-compose.yml with bot service + Redis service
    - Environment variable passthrough via `.env.example`
    - _Requirements: 11.3_

  - [x] 11.3 Implement session expiry and reassignment handling
    - Handle expired session callbacks with message "Sesi inspeksi telah berakhir. Silakan ketik /mulai untuk memulai ulang."
    - Handle motor reassignment detection on resume attempt
    - Handle Frappe 403 on submit → delete session + access denied message
    - _Requirements: 9.6, 15.1, 15.2, 15.3, 15.4_

  - [ ]* 11.4 Write integration tests for full inspection flow
    - Test complete happy path: webhook → notification → /mulai → select motor → checklist → STNK → photos → summary → submit
    - Test resume flow after disconnect
    - Test revision flow with STNK change
    - Use fakeredis + mock Frappe HTTP responses
    - _Requirements: 1.1–1.8, 3.1–3.8, 4.1–4.10, 5.1–5.8, 6.1–6.9, 7.1–7.8, 8.1–8.11_

- [x] 12. Final checkpoint — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document (14 properties total)
- Unit tests validate specific examples and edge cases
- The domain layer is implemented first (pure functions) to enable early property-based testing
- Adapters are implemented after domain to maintain uni-directional dependency (handlers → domain, handlers → adapters; domain does NOT import adapters)
- All code uses Python 3.11 + aiogram 3.x + aiohttp as specified in the design

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "2.1", "2.4", "2.6", "2.9", "2.10", "4.7"] },
    { "id": 3, "tasks": ["2.2", "2.3", "2.5", "2.7", "2.8", "2.11", "2.12", "2.13", "2.14", "2.15"] },
    { "id": 4, "tasks": ["4.1", "4.4", "4.5"] },
    { "id": 5, "tasks": ["4.2", "4.3", "4.6"] },
    { "id": 6, "tasks": ["6.1", "6.2"] },
    { "id": 7, "tasks": ["6.3", "7.1", "7.2"] },
    { "id": 8, "tasks": ["7.3", "8.1", "8.2", "8.3"] },
    { "id": 9, "tasks": ["8.4", "9.1", "9.2"] },
    { "id": 10, "tasks": ["9.3"] },
    { "id": 11, "tasks": ["11.1", "11.2", "11.3"] },
    { "id": 12, "tasks": ["11.4"] }
  ]
}
```
