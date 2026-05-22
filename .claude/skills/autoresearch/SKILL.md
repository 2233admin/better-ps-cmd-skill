---
name: autoresearch
description: Stateful single-mission improvement loop with strict evaluator contract, markdown decision logs, and max-runtime stop behavior
argument-hint: "[--mission-dir <path>] [--max-runtime <duration>] [--cron <spec>] [--resume <run-id>]"
trigger-keywords: "autoresearch loop", "iterate until", "run until pass", "self-improve loop"
level: 4
---

<Purpose>
Autoresearch is a stateful skill for bounded, evaluator-driven iterative improvement. It owns one mission at a time, keeps iterating through non-passing results, records each evaluation and decision as durable artifacts, and stops only when an explicit max-runtime ceiling or another explicit terminal condition is reached.
</Purpose>

<Quick_Start>
```bash
# Resume a previous run
/autoresearch --resume <run-id>

# Start new iteration loop with 30-minute budget
/autoresearch --mission-dir .omc/autoresearch/my-mission --max-runtime 30m

# Schedule periodic runs via cron (every weekday at 9am)
/autoresearch --mission-dir .omc/autoresearch/my-mission --cron "0 9 * * 1-5"
```
</Quick_Start>

<Use_When>
- You already have a mission and evaluator from `/deep-interview --autoresearch`
- You want persistent single-mission improvement with strict evaluation
- You need durable experiment logs under `.omc/autoresearch/`
- You want a supported path for periodic reruns via Claude Code native cron
</Use_When>

<Do_Not_Use_When>
- You need evaluator generation at runtime — use `/deep-interview --autoresearch` first
- You need multiple missions orchestrated together — v1 forbids that
- You want the deprecated `omc autoresearch` CLI flow — it is no longer authoritative
</Do_Not_Use_When>

<Contract>
- Single-mission only in v1
- Mission setup/evaluator generation stays in `deep-interview --autoresearch`
- Evaluator output must be structured JSON with required boolean `pass` and optional numeric `score`
- Non-passing iterations do **not** stop the run
- Stop conditions are explicit and bounded, with max-runtime as the primary strict stop hook

**Evaluator JSON Schema:**
```json
{
  "pass": true,           // required: boolean
  "score": 0.85,          // optional: numeric 0-1
  "message": "...",       // optional: human-readable summary
  "details": {            // optional: structured diagnostics
    "criteria_a": true,
    "criteria_b": 0.9
  }
}
```

**Parameter Definitions:**
- `--mission-dir <path>`: Absolute or relative path to mission directory containing `mission.md` and `evaluator.json`
- `--max-runtime <duration>`: Stop ceiling in minutes (e.g., `30m`), hours (e.g., `2h`), or cycles (e.g., `10x`)
- `--cron <spec>`: Standard cron expression (5-field format)
- `--resume <run-id>`: Continue from a previous run's artifacts under `.omc/logs/autoresearch/<run-id>/`
</Contract>

<Required_Artifacts>
Canonical persistent storage lives under `.omc/autoresearch/<mission-slug>/` and/or `.omc/logs/autoresearch/<run-id>/`.

Minimum required artifacts:
- mission spec
- evaluator script or command reference
- per-iteration evaluation JSON
- markdown decision logs

Recommended canonical shape:
```text
.omc/autoresearch/<mission-slug>/
  mission.md
  evaluator.json
  runs/<run-id>/
    evaluations/
      iteration-0001.json
      iteration-0002.json
    decision-log.md
```
Reuse existing runtime artifacts when available rather than duplicating them unnecessarily.
</Required_Artifacts>

<Workflow>
1. Confirm a single mission exists and evaluator setup is already available.
   **Checkpoint:** Verify mission.md and evaluator.json are present; if missing, abort with error.
2. Ensure mode/state is active for `autoresearch` and records:
   - mission slug/dir
   - evaluator reference
   - iteration count
   - started/updated timestamps
   - explicit max-runtime or deadline
   **Checkpoint:** Show mission summary to user; confirm "Start iteration loop?" before proceeding.
3. On every iteration:
   - run exactly one experiment/change cycle
   - run the evaluator
   - persist machine-readable evaluation JSON
   - append a human-readable markdown decision log entry
   - continue even when evaluation does not pass
   **Checkpoint:** After each iteration, display score and pass/fail; ask "Continue to next iteration?"
4. Stop when:
   - max-runtime ceiling is reached
   - user explicitly cancels
   - another explicit terminal condition is recorded by the runtime
   **Checkpoint:** On stop, show final summary with all iteration results.

**Decision Log Entry Format:**
```markdown
## Iteration 0003 (2026-05-23T14:32:00Z)

**Previous state:** score=0.72, pass=false
**Action taken:** Refactored error handling to use Result types
**New score:** 0.81
**Pass:** false
**Next direction:** Extract validation logic into separate module
```
</Workflow>

<Cron_Integration>
Claude Code native cron is a supported integration point for periodic mission enhancement. In v1, prefer documenting/configuring cron inputs over building a large scheduler UI.

If cron is used:
- keep one mission per scheduled job
- preserve the same mission/evaluator contract
- append new run artifacts rather than overwriting prior experiments
</Cron_Integration>

<Execution_Policy>
- Do not hand execution back to `omc autoresearch`
- Do not create multi-mission orchestration
- Prefer reusing `src/autoresearch/*` runtime/schema helpers where they already match the stricter contract
- Keep logs useful to humans, not only machines
</Execution_Policy>
