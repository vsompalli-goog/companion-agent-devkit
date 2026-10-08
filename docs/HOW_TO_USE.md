# How to Use the Agent Assist Companion Agent DevKit (`aa-dev-kit`)

This guide walks **Google Forward Deployed Engineers (FDEs)** and **Customer Engineers (CEs)** step-by-step through installing and using `aa-dev-kit` across **Milestones L1 – L4** (`Sections 7.2 – 7.8` of the PRD).

---

## 1. Prerequisites & Installation

### Requirements
- **Python**: `3.10+`
- **GCP Authentication** *(only required when interacting with a live GCP project for `export`, `eval --live`, or `voice-synth`/`voice-replay`)*:
  ```bash
  gcloud auth application-default login
  ```

### Install `aa-devkit`
From the `companion-agent-devkit` repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

# Verify CLI installation
aa-devkit --help
```

> **Stateless & Zero-Deploy Boundary**: Every `aa-devkit` command operates strictly on local JSON folders or `.zip` files without modifying deployed Companion Agent configurations. Configuration deployment, Git repository versioning (`git diff` / `git commit`), PR approvals, IAM write grants, and rollbacks (`git revert`) remain completely under your own Git/CI/CD workflow.

---

## 2. Milestone L1: Skills Pack, Migration & Golden Regression Evals (`FR-1.1` – `FR-1.5`)

### 2.1 Install the Single-Source Skills Pack (`FR-1.1`)

`aa-devkit install-pack` compiles `pack/` into native customization folders for your AI coding agent:

```bash
# Option A: For Google FDEs & Engineers Using Jetski (.agent)
aa-devkit install-pack --target agent --dest /path/to/your/workspace

# Option B: For Customer Engineers Using Claude Code (.claude)
aa-devkit install-pack --target claude --dest /path/to/customer/workspace
```

**Installed Skills**:
- `aa-companion-architect` (`FR-1.1`, `FR-1.2`, `FR-1.4`, `FR-6.1`): Authoring best practices, Reactive vs. Proactive & Tool-Type Decision Guides, Workflow/AnswerRecord APIs, UI Concurrency Bridge, and 6-Stage Latency Taxonomy.
- `aa-migration` (`FR-1.3`): AI Coach (`agentCoachingContext`) and PGKA/GKA $\rightarrow$ Companion Agent migration guide.
- `aa-config-review` (`FR-2.2`): Guided P0/P1/P2 configuration audit workflow.
- `aa-eval-hillclimb` (`FR-7.1` – `FR-7.3`): Eval-driven hill-climbing workflow.

> **Privacy Rule (`FR-1.1`)**: Third-party AI packages (such as `.claude`) are strictly for customers' own use inside their own environment. Google staff must **never** load customer data into third-party AI tools.

### 2.2 Migrate Legacy AI Coach & PGKA Configurations (`FR-1.3`)

Convert a legacy Dialogflow v2beta1 Generator (`agentCoachingContext`) JSON into the modern Companion Agent (`skillConfigs` + `cesToolSpecs`) schema while automatically rewriting `{$tool.*}` $\rightarrow$ `{@TOOL:*}`, rescuing UI-only `displayDetails`, and adding `"triggerEvent": "CUSTOMER_MESSAGE"` to intake/verification cards:

```bash
aa-devkit migrate-coach ./legacy_ai_coach_generator.json \
  --output ./migrated_companion_agent.json
```

### 2.3 Run Golden-Question Skill Regression Evals (`FR-1.5`)

Verify that all skills and progressive-disclosure references satisfy the 13 golden engineering questions in `evals/golden_questions.json`:

```bash
aa-devkit eval-skills
```

---

## 3. Milestone L2: Stateless Config Export & Best-Practice Review (`FR-2.1`, `FR-2.2`)

### 3.1 Export a Companion Agent Configuration Bundle (`FR-2.1`)

Deep-harvest `ConversationProfile`, `CompanionAgent`, linked `CompanionAgentWorkflows`, and `CES`/`Dialogflow` Tools via pure REST (`v2beta1` + `v1beta`):

```bash
# Export to a .zip archive ("zip route first")
aa-devkit export \
  --profile "projects/my-gcp-project/locations/global/conversationProfiles/my-profile-id" \
  --env prod \
  --output ./companion_agent_bundle.zip

# Or export directly into a local directory
aa-devkit export \
  --agent "projects/my-gcp-project/locations/us-central1/companionAgents/my-agent-id" \
  --env staging \
  --output ./companion_agent_bundle/
```

### 3.2 Run Deterministic Best-Practice Config Review (`FR-2.2`)

```bash
# Print prioritized Markdown report (P0 / P1 / P2)
aa-devkit review ./companion_agent_bundle.zip

# Return exit code 1 if any P0 critical blocker exists (use as a pre-deploy CI gate)
aa-devkit review ./companion_agent_bundle.zip --format json --fail-on-p0
```

---

## 4. Milestone L3: CLI Evals, Voice Testing & Latency (`Sections 7.5 – 7.7`)

### 4.1 Turn-by-Turn CLI Evaluation Pipeline (`FR-4.1`, `FR-4.2`)

Score multi-turn conversations against expected guidance cards, tool calls, tool parameters, and negative suppression (`must_suppress: true` on greetings/filler turns):

```bash
# Offline evaluation against recorded AnalyzeContentResponse payloads
aa-devkit eval evals/samples/sample_eval_conversations.json

# Live evaluation against a deployed ConversationProfile in the customer's GCP project
aa-devkit eval ./customer_eval_set.json \
  --live \
  --profile "projects/my-customer-project/locations/global/conversationProfiles/my-profile-id"
```

### 4.2 Voice Testing Environment: Stock-Voice TTS & Streaming Replay (`FR-5.1` – `FR-5.3`)

Synthesize WAV audio turns from transcripts using **approved stock Google Cloud TTS voices only** (voice cloning is strictly prohibited per `FR-5.1`; every artifact is tagged `"derivedFromCustomerData": true`) and replay them with per-customer codec, chunk size, cadence, and jitter settings:

```bash
# 1. Synthesize turn-by-turn audio in the customer's GCP project (FR-5.1, FR-5.3)
aa-devkit voice-synth evals/samples/sample_eval_conversations.json \
  --project my-customer-project \
  --caller-voice en-US-Journey-F \
  --agent-voice en-US-Journey-D \
  --codec AUDIO_ENCODING_LINEAR_16 \
  --sample-rate-hz 24000 \
  --chunk-ms 100 \
  --cadence-ms 100 \
  --jitter-ms 10 \
  --output-dir ./synth_voice_bundle

# 2. Replay synthesized audio turns against the customer's ConversationProfile (FR-5.2)
aa-devkit voice-replay ./synth_voice_bundle \
  --profile "projects/my-customer-project/locations/global/conversationProfiles/my-profile-id" \
  --simulate-pacing
```

### 4.3 6-Stage Call Latency Breakdown & Observability (`FR-6.1` – `FR-6.4`)

Generate per-call and aggregate latency distributions (`p50`, `p90`, `p95`, `p99`, `max`) across all 6 canonical call stages (`customer_integration_network`, `ui_bridge`, `speech_endpointing_stt`, `llm_generation_vertex`, `tool_execution`, `quota_throttling`), automatically merging API `observabilityMetrics` when present:

```bash
# Markdown latency table
aa-devkit latency-report evals/samples/sample_eval_conversations.json

# Self-contained HTML visual dashboard
aa-devkit latency-report evals/samples/sample_eval_conversations.json \
  --format html \
  --output ./latency_dashboard.html
```

---

## 5. Milestone L4: Eval-Driven Hill-Climbing Iteration (`FR-7.1` – `FR-7.3`)

Run a customer-project-scoped (`PRIV-13`) evaluation and optimization loop that always estimates Vertex AI token/USD cost first (`FR-7.2`), clusters evaluation failures into actionable loss patterns (`FALSE_POSITIVE_TRIGGER`, `MISSED_GUIDANCE_CARD`, `MISSED_TOOL_CALL`, `TOOL_PARAMETER_MISMATCH`), and writes a candidate bundle for human review and deployment via your Git/CI/CD pipeline:

```bash
# 1. Pre-run Vertex AI token & USD cost estimate only (FR-7.2)
aa-devkit hillclimb \
  --bundle ./companion_agent_bundle/ \
  --dataset evals/samples/sample_eval_conversations.json \
  --iterations 3 \
  --estimate-only

# 2. Run evaluation, cluster loss patterns, and emit candidate bundle (FR-7.1, FR-7.3)
aa-devkit hillclimb \
  --bundle ./companion_agent_bundle/ \
  --dataset evals/samples/sample_eval_conversations.json \
  --candidate-output ./candidate_bundle/

# 3. Validate candidate bundle before deploying through your Git/CI pipeline
aa-devkit review ./candidate_bundle/ --fail-on-p0
```
