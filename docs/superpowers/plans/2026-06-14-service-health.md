# Service Health Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `aw-coach service status` show whether the background daemon is installed, running, and recently ticking.

**Architecture:** Persist scheduler heartbeat JSON in the existing `scheduler_state` table, extend `ServiceStatus` with daemon process IDs, and let CLI service commands combine installer state, heartbeat state, and log paths. Keep each responsibility local: scheduler writes health, service installer inspects Windows process/autostart state, CLI renders user-facing diagnostics.

**Tech Stack:** Python standard library (`json`, `os`, `datetime`, `pathlib`, PowerShell process inspection), Click CLI, SQLite-backed `Storage`, pytest.

---

### Task 1: Scheduler Heartbeat

**Files:**
- Modify: `src/aw_coach/scheduler.py`
- Test: `tests/test_notify.py`

- [ ] **Step 1: Write failing scheduler heartbeat tests**

Add tests that construct `CoachScheduler(Config(db_path=tmp_path / "coach.db"))`, call a new `_write_service_health()` helper with a fixed `datetime`, and assert `scheduler.storage.get_scheduler_state("service_health")` contains JSON with `pid`, `last_tick`, `status`, and `last_success`.

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_notify.py -q
```

Expected: fail because `_write_service_health` does not exist.

- [ ] **Step 3: Implement heartbeat helper and loop integration**

Add scheduler fields `_started_at`, `_last_service_error`, `_last_service_error_at`. Implement `_write_service_health(now, status="running", error=None)` to write JSON under key `service_health`.

Call it:

- once after startup setup with status `running`
- after each scheduler loop, recording top-level loop errors
- in `_shutdown()` with status `stopped`

- [ ] **Step 4: Run tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_notify.py -q
```

Expected: pass.

### Task 2: Service Status Process and Heartbeat Rendering

**Files:**
- Modify: `src/aw_coach/service_installer.py`
- Modify: `src/aw_coach/cli.py`
- Test: `tests/test_service_installer.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add tests that:

- `ServiceManager.status()` parses `PID:1234` lines into `ServiceStatus.daemon_pids == (1234,)`.
- `aw-coach service status` prints daemon process IDs, heartbeat freshness, last tick, and log paths when mocked health exists.

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_service_installer.py tests/test_cli.py -q
```

Expected: fail because `daemon_pids` and heartbeat rendering are missing.

- [ ] **Step 3: Implement process parsing and CLI rendering**

Extend `ServiceStatus` with `daemon_pids: tuple[int, ...] = ()`. Make `_status_script()` output state plus `PID:<id>` lines for matching daemon processes. Parse those lines in `status()`.

In CLI, read `Storage(config.db_path).get_scheduler_state("service_health")`, parse JSON, classify heartbeat as fresh when `last_tick` is no older than 180 seconds, and print log file paths under `config.data_dir`.

- [ ] **Step 4: Run tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_service_installer.py tests/test_cli.py -q
```

Expected: pass.

### Task 3: Service Logs and Doctor Line

**Files:**
- Modify: `src/aw_coach/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing tests**

Add tests for:

- `aw-coach service logs --lines 2` printing the tail of `aw-coach-daemon.log` and `aw-coach-daemon.err.log`.
- `aw-coach doctor` including a service/autostart health line when service status is available.

- [ ] **Step 2: Run tests to verify failure**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_cli.py -q
```

Expected: fail because `service logs` and doctor service line are missing.

- [ ] **Step 3: Implement CLI additions**

Add `service logs --lines N` with a small file-tail helper. Add a non-fatal service health line in `doctor`, guarded so unsupported platforms or service errors do not break doctor.

- [ ] **Step 4: Run tests**

Run:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests/test_cli.py -q
```

Expected: pass.

### Task 4: Final Verification

**Files:**
- No code edits unless verification reveals a bug.

- [ ] **Step 1: Run full checks**

```powershell
python -m ruff check .
$env:PYTHONPATH='src'; python -m pytest tests/ -p no:anyio -q
```

- [ ] **Step 2: Restart daemon and verify real status**

```powershell
$env:PYTHONPATH='src'
python -c "from aw_coach.cli import main; main()" service stop
python -c "from aw_coach.cli import main; main()" service start
Start-Sleep -Seconds 10
python -c "from aw_coach.cli import main; main()" service status
python -c "from aw_coach.cli import main; main()" service logs --lines 20
python -c "from aw_coach.cli import main; main()" doctor
```
