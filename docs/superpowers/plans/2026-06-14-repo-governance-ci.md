# Repository Governance and CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the new GitHub repository usable as a stable project with a `main` baseline, CI checks, and Windows service usage documentation.

**Architecture:** Keep `main` as the stable branch at the pre-autostart baseline, keep `feature/windows-autostart` as the active feature branch, and add CI/documentation on the feature branch. GitHub Actions will run the same local validation commands used during development.

**Tech Stack:** Git/GitHub CLI, GitHub Actions, Python 3.11, pip editable installs, pytest, ruff, Markdown documentation.

---

### Task 1: Create Stable Main Branch

**Files:**
- No file edits.

- [ ] **Step 1: Create local `main` at the previous stable commit**

Run:

```powershell
git branch main 5edbaf2
```

Expected: local `main` points at `Add on-demand AI summary panel`.

- [ ] **Step 2: Push `main`**

Run:

```powershell
git push -u origin main
```

Expected: remote branch `main` exists.

- [ ] **Step 3: Set GitHub default branch to `main`**

Run:

```powershell
gh repo edit Renoyz/aw-watcher-ai-coach --default-branch main
```

Expected: `gh repo view --json defaultBranchRef` reports `main`.

### Task 2: Add CI Workflow

**Files:**
- Create: `.github/workflows/ci.yml`

- [ ] **Step 1: Add workflow file**

Create `.github/workflows/ci.yml` with checkout, Python setup, dependency install, ruff, and pytest.

- [ ] **Step 2: Validate YAML parses**

Run:

```powershell
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8')); print('ok')"
```

Expected: `ok`.

### Task 3: Add README

**Files:**
- Create: `README.md`

- [ ] **Step 1: Document setup and Windows service commands**

Create README sections for prerequisites, install, config, service install/start/status/logs/doctor, privacy defaults, and development checks.

- [ ] **Step 2: Verify key commands are documented**

Run:

```powershell
python - <<'PY'
from pathlib import Path
text = Path("README.md").read_text(encoding="utf-8")
for phrase in ["aw-coach service install", "aw-coach service status", "aw-coach service logs", "python -m ruff check ."]:
    assert phrase in text, phrase
print("ok")
PY
```

Expected: `ok`.

### Task 4: Final Verification and PR

**Files:**
- No code edits unless verification reveals a bug.

- [ ] **Step 1: Run local checks**

Run:

```powershell
python -m ruff check .
$env:PYTHONPATH='src'; python -m pytest tests/ -p no:anyio -q
```

Expected: both pass.

- [ ] **Step 2: Commit and push**

Run:

```powershell
git add .github README.md docs/superpowers/plans/2026-06-14-repo-governance-ci.md
git commit -m "Add repository CI and Windows usage docs"
git push
```

- [ ] **Step 3: Open draft PR**

Run:

```powershell
gh pr create --draft --base main --head feature/windows-autostart --title "Add Windows autostart service health diagnostics"
```

Expected: GitHub draft PR exists.
