---
name: aa-eval-hillclimb
description: Run evaluation-driven hill-climbing loops (Section 7.8: FR-7.1, FR-7.2, FR-7.3) for Google Cloud Agent Assist Companion Agents. Use when estimating Vertex AI evaluation costs before execution, scoring turn-by-turn evaluation datasets, clustering loss patterns (false-positive triggers, missed guidance cards, missed tool calls, parameter mismatches), and proposing local JSON configuration fixes in the customer's GCP project (PRIV-13).
---

# Companion Agent Eval-Driven Hill-Climbing (L4)

Use this skill to iteratively improve a Companion Agent's accuracy, negative suppression rate, and tool invocation precision using the `aa-devkit` evaluation and hill-climbing pipeline.

## Mandatory Safety & Privacy Guardrails (`FR-7.2`, `FR-7.3`, `PRIV-13`)

1. **Pre-Run Cost Estimation (`FR-7.2`)**:
   - Always calculate and display the estimated token usage and Vertex AI USD cost (`aa-devkit hillclimb --estimate-only` or pre-run summary) **before** running live `:analyzeContent` evaluation loops.
2. **Customer-Project Scope (`FR-7.3` / `PRIV-13`)**:
   - All live evaluations, voice replays, and hill-climbing runs execute strictly inside the **customer's designated GCP project** (`--project` / `X-Goog-User-Project`).
   - Customer transcripts, `<call_context>`, and evaluation outputs must **never** be committed to the DevKit repository or loaded by Google staff into external third-party AI tools.
3. **User-Owned Git & CI/CD Deployment Boundary**:
   - Hill-climbing writes proposed configuration improvements to a local candidate bundle (`--candidate-output`).
   - Always run `aa-devkit review --fail-on-p0` on the candidate bundle. Versioning (`git diff` / `git commit`), PR approvals, deployment to GCP, and rollbacks (`git revert`) are handled exclusively by the user's own Git/CI/CD pipeline.

## 4-Step Hill-Climbing Loop (`FR-7.1`)

### Step 1: Estimate Cost & Run Baseline Evaluation
```bash
# 1a. Preview estimated Vertex AI tokens and cost (FR-7.2)
aa-devkit hillclimb \
  --bundle ./exported_bundle \
  --dataset ./evals/samples/sample_eval_conversations.json \
  --estimate-only

# 1b. Run evaluation & generate loss-pattern analysis + candidate bundle
aa-devkit hillclimb \
  --bundle ./exported_bundle \
  --dataset ./evals/samples/sample_eval_conversations.json \
  --candidate-output ./candidate_bundle
```

### Step 2: Inspect Loss Patterns & Refine Candidate Bundle
Review the four canonical loss categories produced by `aa-devkit hillclimb`:
- `FALSE_POSITIVE_TRIGGER`: Greeting/filler turns triggered a card instead of remaining silent (`must_suppress: true`). Fix by setting `"triggerEvent": "CUSTOMER_MESSAGE"` on intake/verification cards and tightening overly broad `condition` statements.
- `MISSED_GUIDANCE_CARD`: Expected card failed to fire. Fix by splitting monolithic `IF/ELSE` conditions (`One Scenario = One Card`) and adding concrete customer intent phrasings to `condition`.
- `MISSED_TOOL_CALL`: Card fired without invoking `{@TOOL:tool_name}`. Fix by binding `{@TOOL:tool_name}` directly inside an observable `actions[]` checkpoint step and verifying `cesToolSpecs` has `"proactiveEnabled": true`.
- `TOOL_PARAMETER_MISMATCH`: Tool invoked with wrong or hallucinated parameters. Fix by clarifying parameter extraction rules in the CES Python/OpenAPI docstring (`Args:`) and prohibiting invented fallback values.

### Step 3: Validate Candidate Bundle
```bash
# Deterministic best-practice & P0 guardrail check (FR-2.2)
aa-devkit review ./candidate_bundle --fail-on-p0

# Inspect changes using standard Git diff
git diff --no-index ./exported_bundle ./candidate_bundle
```

### Step 4: Deploy via User's CI/CD & Re-Evaluate
Commit the validated candidate bundle to your configuration Git repository, deploy through your audited CI/CD pipeline, and re-run `aa-devkit eval --live` in the customer's GCP project to verify the accuracy improvement.
