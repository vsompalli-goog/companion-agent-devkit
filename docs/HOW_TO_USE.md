# How to Use the Agent Assist Companion Agent DevKit (`aa-dev-kit`)

This guide walks **Google Forward Deployed Engineers (FDEs)** and **Customer Engineers (CEs)** step-by-step through installing and using `aa-dev-kit` across **Milestones L1 – L4** (`Sections 7.2 – 7.8` of the PRD) and the integrated **Companion Agent Simulator Suite**, using ready-to-run sample files included under `evals/samples/`.

---

## 1. Prerequisites & Installation

### Requirements
- **Python**: `3.10+`
- **GCP Authentication** *(only required when interacting with a live GCP project for `export`, `eval --live`, or live Cloud TTS `voice-synth`/`voice-replay`; all offline commands run with zero cloud credentials)*:
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
```

### 3.2 Run Deterministic Best-Practice Config Review (`FR-2.2`)

You can test `aa-devkit review` immediately against the included sample bundle at `evals/samples/sample_agent_bundle`:

```bash
# Print prioritized Markdown report (P0 / P1 / P2)
aa-devkit review evals/samples/sample_agent_bundle

# Return exit code 1 if any P0 critical blocker exists (use as a pre-deploy CI gate)
aa-devkit review evals/samples/sample_agent_bundle --format json --fail-on-p0
```

---

## 4. Simulator Suite: Transcript Import, Scenario Generation & 5D Persona Simulation

### 4.1 3-Stage Universal Transcript Importer & `[REDACTED]` PII Healer (`aa-devkit import-transcript`)

Converts raw contact-center logs into the `FR-4.2` evaluation dataset schema using a 3-stage pipeline:
1. **Stage 1 (JSON Schema Fingerprinting)**: Automatically detects **Genesys Cloud** (`transcripts` $\rightarrow$ `phrases`), **Amazon Connect Contact Lens** (`Transcript` $\rightarrow$ `ParticipantId`), **Google Cloud CCAI** (`entries`), and generic JSON arrays.
2. **Stage 2 (CSV/TSV & Call-Log Regex Heuristics)**: Parses CSV/TSV exports and timestamped call logs (`[00:00:05] Agent (Sarah): ...`), normalizes speaker roles (`END_USER`, `HUMAN_AGENT`), merges consecutive turns from the same speaker, and auto-tags greeting/filler turns with `"must_suppress": true`.
3. **Stage 3 (Consistent `[REDACTED]` PII Healer)**: Replaces `[REDACTED]`, `[REDACTED_NAME]`, `[REDACTED_ORDER]`, `[REDACTED_EMAIL]`, `[PII]`, `***`, and `XXXX` with realistic, context-aware synthetic entities that remain **internally consistent across every turn of the call** (e.g., if the caller gives a redacted order ID and email in Turn 3 and the agent repeats them in Turn 4, both turns receive `ORD-849201` and `alex.rivera@example.com`).

#### Try It Now with Included Samples:

```bash
# Sample 1: Import a Genesys Cloud transcript with [REDACTED_*] PII placeholders
aa-devkit import-transcript evals/samples/sample_genesys_transcript.json \
  --output /tmp/imported_genesys.json

# Sample 2: Import a timestamped plain-text call log with [REDACTED] placeholders
aa-devkit import-transcript evals/samples/sample_call_log.txt \
  --output /tmp/imported_call_log.json

# Sample 3: Convert human-agent turns to dynamic SUGGESTION mode for live simulation
aa-devkit import-transcript evals/samples/sample_genesys_transcript.json \
  --agent-mode SUGGESTION \
  --output /tmp/imported_suggestion_mode.json
```

---

### 4.2 Config-Aware Synthetic Scenario Generator (`aa-devkit generate-scenarios`)

Generates multi-turn evaluation datasets directly from an exported `CompanionAgent` bundle (`companion_agent.json`, `workflows/*.json`, `tools/*.json`):
- **Multi-Step Workflow Pacing Rule (`1 workflow step = 2 turns`)**: Automatically inspects all workflows in the bundle and expands the turn count to `max(requested_turns, min(36, max_workflow_steps * 2 + 2))` so multi-step workflows have enough turns to complete every step.
- **Negative Suppression Small-Talk Interleaving (`Rule 1B`)**: Injects opening greetings and closing thank-you turns marked with `"must_suppress": true`.
- **Schema-Aligned Ground Truth**: Populates `expected_guidance_cards`, `expected_tools`, and `expected_entities` from your bundle's configured guidance instructions and tool `inputSchema` parameters.

#### Try It Now with the Included Sample Bundle:

```bash
# The sample bundle has a 3-step workflow (`order_refund_workflow.json`), so requesting
# --turns 4 automatically expands to 8 paced turns (3 steps * 2 + 2 greeting/closing turns):
aa-devkit generate-scenarios \
  --bundle evals/samples/sample_agent_bundle \
  --count 2 \
  --turns 4 \
  --output /tmp/generated_scenarios.json
```

---

### 4.3 5D Dynamic Customer Persona & 4-Strategy Agent Perturbation Harness (`aa-devkit simulate` / `FR-5.5`)

Stress-tests how your Companion Agent handles realistic caller behavior and imperfect human agents:
- **5D Dynamic Customer Persona**:
  - `--tone`: `polite`, `confused`, `frustrated`, `rushed`, `adversarial`
  - `--tech-literacy`: `low` (drip-feeds **at most 1** slot per turn), `medium` (1–2 slots), `high` (up to 2 slots with precise terminology)
  - `--patience`: `1`–`5` (automatically decays when the human agent repeats themselves or uses stalling phrases, shifting caller tone `polite` $\rightarrow$ `frustrated` $\rightarrow$ `adversarial` and triggering a supervisor escalation request when patience hits `1`)
  - `--escalation-tendency`: `low`, `medium`, `high`
  - `--verbosity`: `concise`, `normal`, `verbose`
- **4-Strategy Virtual Human Agent Perturbation (`--agent-strategy`)**:
  - `strict`: Follows the Companion Agent's guidance card verbatim.
  - `paraphrase`: Rephrases the guidance card in the agent's own words while preserving slot-filling questions.
  - `deviate`: Deliberately ignores the guidance card on targeted turns to test whether the Companion Agent recovers on subsequent turns.
  - `probe_negative`: Injects low-signal hold/filler turns (`"One moment please."`) to test negative suppression.

#### Try It Now with the Included Sample Bundle:

```bash
# Simulate a confused, low-tech-literacy caller paired with a paraphrasing human agent
aa-devkit simulate \
  --bundle evals/samples/sample_agent_bundle \
  --count 1 \
  --turns 8 \
  --tone confused \
  --tech-literacy low \
  --patience 3 \
  --escalation-tendency high \
  --verbosity concise \
  --agent-strategy paraphrase \
  --output /tmp/simulated_5d.json
```

---

## 5. Milestone L3: CLI Evals, Ground-Truth Bootstrapper, Voice DSP & Latency (`Sections 7.5 – 7.7`)

### 5.1 Turn-by-Turn Evaluation Pipeline with Layer 0 State-Machine Checks & Strict/Forgiving F1 (`FR-4.1`, `FR-4.2`)

`aa-devkit eval` scores conversations across three layers:
1. **Layer 0 Cross-Turn `PayloadIntegrityValidator`**:
   - Flags `NEEDS_CONFIRMATION` tool calls missing an `answerRecord` (`[PAYLOAD_ANOMALY]`).
   - Flags illegal backward `answerRecord` state transitions from terminal states (`COMPLETED` $\rightarrow$ `NEEDS_CONFIRMATION`) (`[STATE_MACHINE_BUG]`).
   - Flags **Unconfirmed HITL Execution Bypasses** (`[ENGINE_BUG] UNCONFIRMED_EXECUTION_BYPASS`) when a tool configured with `confirmationRequirement: "REQUIRED"` in `--bundle` transitions to `CALLED`/`COMPLETED` without prior confirmation.
   - Flags empty `knowledgeSources` citation blocks (`[CITATION_ANOMALY]`).
2. **Guidance Card, Tool & Negative Suppression Scoring**:
   - Verifies `expected_guidance_cards`, `expected_tools`, and automatic low-signal utterance suppression (`_LOW_SIGNAL_UTTERANCE_RE`), while carrying forward cumulative RAG grounding snippets across turns.
3. **Session-Level Strict (Turn-Exact) vs. Forgiving (Session-Level) Entity F1**:
   - Flattens arbitrarily nested JSON parameters (`customer.address.zip`, `items.0.sku`), tracks cross-turn `carryover` memory (`ai_memory`), and computes both **Strict** and **Forgiving** `Precision`, `Recall`, `F1`, and `Accuracy`.
   - Pass `--baseline-run` to compute turn-level and F1 regression deltas against a previous evaluation run.

#### Try It Now with Included Samples:

```bash
# Run offline evaluation with bundle-aware HITL checks and baseline regression comparison
aa-devkit eval evals/samples/sample_eval_conversations.json \
  --bundle evals/samples/sample_agent_bundle \
  --baseline-run evals/samples/sample_eval_conversations.json

# Run live evaluation against a deployed ConversationProfile in the customer's GCP project
aa-devkit eval ./customer_eval_set.json \
  --live \
  --profile "projects/my-customer-project/locations/global/conversationProfiles/my-profile-id" \
  --bundle ./companion_agent_bundle/
```

---

### 5.2 Ground-Truth Dataset Bootstrapper (`aa-devkit bootstrap-eval`)

When you have recorded `:analyzeContent` responses (`actual_response`) from a known-good session, `aa-devkit bootstrap-eval` reverse-engineers the `expected` block for every turn — computing **turn-over-turn entity deltas** so each turn only requires newly extracted parameters, and tagging silent turns with `"must_suppress": true`:

```bash
aa-devkit bootstrap-eval evals/samples/sample_eval_conversations.json \
  --output /tmp/bootstrapped_eval_dataset.json

# Verify the bootstrapped dataset achieves 100% pass rate and 100% Strict/Forgiving F1
aa-devkit eval /tmp/bootstrapped_eval_dataset.json
```

---

### 5.3 Voice Testing Environment & PSTN Telephony DSP Conditioning (`FR-5.1` – `FR-5.3`)

Synthesizes WAV audio turns from transcripts using **approved stock Google Cloud TTS voices only** (voice cloning is strictly prohibited per `FR-5.1`; every artifact is tagged `"derivedFromCustomerData": true`) with built-in prosody normalization and telephony DSP conditioning:
- **Prosody Normalizer & Sentence Chunker**: Strips stage directions (`(frustrated tone)`, `[sighs]`), markdown, and wrapping quotes, and splits long turns at sentence/clause boundaries (`<=200` chars).
- **`--telephone-filter`**: Applies a pure-Python G.711 PSTN bandpass filter (`190 Hz – 3100 Hz`) and soft-knee telephony saturation.
- **`--noise-profile {none,call_center,white_noise}`**: Mixes realistic call-center speech-band babble or white noise to test STT robustness.
- **`--include-transfer-ring`**: Prepends a North American dual-tone (`440 Hz + 480 Hz`) PSTN ringback tone (`turn_000_transfer_ring.wav`).
- **`--stereo`**: Exports a full-call 2-channel stereo WAV file (`full_call_stereo.wav`) with **Caller (`END_USER`) on the Left channel** and **Agent (`HUMAN_AGENT`) on the Right channel**.
- **`--offline`**: Generates speech-cadence harmonic test WAVs locally without making billable Cloud TTS API calls.

#### Try It Now (Offline Local DSP Mode & Live Cloud TTS Mode):

```bash
# 1A. Test locally with zero GCP credentials using --offline + full PSTN DSP conditioning
aa-devkit voice-synth evals/samples/sample_eval_conversations.json \
  --offline \
  --telephone-filter \
  --noise-profile call_center \
  --stereo \
  --include-transfer-ring \
  --output-dir /tmp/synth_voice_dsp

# 1B. Synthesize in the customer's GCP project using Cloud TTS Stock Voices (FR-5.1, FR-5.3)
aa-devkit voice-synth evals/samples/sample_eval_conversations.json \
  --project my-customer-project \
  --caller-voice en-US-Journey-F \
  --agent-voice en-US-Journey-D \
  --codec AUDIO_ENCODING_LINEAR_16 \
  --sample-rate-hz 24000 \
  --chunk-ms 100 \
  --cadence-ms 100 \
  --jitter-ms 10 \
  --telephone-filter \
  --noise-profile call_center \
  --stereo \
  --output-dir ./synth_voice_bundle

# 2. Replay synthesized audio turns against the customer's ConversationProfile (FR-5.2)
aa-devkit voice-replay ./synth_voice_bundle \
  --profile "projects/my-customer-project/locations/global/conversationProfiles/my-profile-id" \
  --simulate-pacing
```

---

### 5.4 6-Stage Call Latency Breakdown & Observability (`FR-6.1` – `FR-6.4`)

Generate per-call and aggregate latency distributions (`p50`, `p90`, `p95`, `p99`, `max`) across all 6 canonical call stages (`customer_integration_network`, `ui_bridge`, `speech_endpointing_stt`, `llm_generation_vertex`, `tool_execution`, `quota_throttling`), automatically merging API `observabilityMetrics` when present:

```bash
# Markdown latency table
aa-devkit latency-report evals/samples/sample_eval_conversations.json

# Self-contained HTML visual dashboard
aa-devkit latency-report evals/samples/sample_eval_conversations.json \
  --format html \
  --output /tmp/latency_dashboard.html
```

---

## 6. Milestone L4: Eval-Driven Hill-Climbing Iteration (`FR-7.1` – `FR-7.3`)

Run a customer-project-scoped (`PRIV-13`) evaluation and optimization loop that always estimates Vertex AI token/USD cost first (`FR-7.2`), clusters evaluation failures into actionable loss patterns (`FALSE_POSITIVE_TRIGGER`, `MISSED_GUIDANCE_CARD`, `MISSED_TOOL_CALL`, `TOOL_PARAMETER_MISMATCH`), and writes a candidate bundle for human review and deployment via your Git/CI/CD pipeline:

```bash
# 1. Pre-run Vertex AI token & USD cost estimate only (FR-7.2)
aa-devkit hillclimb \
  --bundle evals/samples/sample_agent_bundle \
  --dataset evals/samples/sample_eval_conversations.json \
  --iterations 3 \
  --estimate-only

# 2. Run evaluation, cluster loss patterns, and emit candidate bundle (FR-7.1, FR-7.3)
aa-devkit hillclimb \
  --bundle evals/samples/sample_agent_bundle \
  --dataset evals/samples/sample_eval_conversations.json \
  --candidate-output /tmp/candidate_bundle/

# 3. Validate candidate bundle before deploying through your Git/CI pipeline
aa-devkit review /tmp/candidate_bundle/ --fail-on-p0
```
