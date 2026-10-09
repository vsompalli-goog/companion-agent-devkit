# Agent Assist Companion Agent DevKit (`aa-dev-kit`)

`aa-dev-kit` packages Google Cloud Contact Center AI (CCAI) **Companion Agent** engineering best practices, decision guides, AI Coach / PGKA migration tools, workflow/UI concurrency samples, and stateless configuration export, review, transcript importing, synthetic scenario generation, 5D persona simulation, evaluation, voice DSP testing, latency observability, and hill-climbing tools for AI coding agents (Jetski `.agent` and Claude Code `.claude`).

> **Privacy Boundary**: Customer data never goes into this kit. Anything that touches customer data runs in the customer's project or local workspace by default (`PRIV-13`). Third-party AI packages (such as `.claude`) are strictly for customers' own use — Google staff must never load customer data into third-party AI tools.

---

## Architecture & Milestones (L1 – L4 + Simulator Suite)

| Level | Capability | Deliverables & CLI Subcommands |
| :--- | :--- | :--- |
| **L1: Best-Practice Skills Pack** (`7.2`) | Authoring rules, reactive vs. proactive & tool-type decision guides, AI Coach/PGKA migration, Workflow/AnswerRecord APIs, UI concurrency bridge, and golden-question regression suite | `pack/rules/companion-agent-guardrails.md`, `pack/skills/aa-companion-architect/`, `pack/skills/aa-migration/`, `aa-devkit install-pack`, `aa-devkit migrate-coach`, `aa-devkit eval-skills` (`FR-1.1` – `FR-1.5`) |
| **L2: Config Export & Review** (`7.3`) | Stateless export of Conversation Profile + Companion Agent + Workflows + CES/Dialogflow Tools to local folder/zip, plus deterministic & AI-assisted best-practice review | `aa-devkit export`, `aa-devkit review`, `pack/skills/aa-config-review/` (`FR-2.1`, `FR-2.2`) |
| **L3: Eval Pipeline, Simulator Suite, Voice DSP & Latency** (`7.5` – `7.7`) | 3-Stage Universal Transcript Importer & PII Healer, Config-Aware Scenario Generator, Layer 0 Cross-Turn State-Machine Validator, Strict vs. Forgiving F1 & Ground-Truth Bootstrapper, 5D Persona & 4-Strategy Agent Perturbation Harness, Stock-Voice TTS + PSTN Telephony DSP, and 6-Stage Latency Taxonomy | `aa-devkit import-transcript`, `aa-devkit generate-scenarios`, `aa-devkit bootstrap-eval`, `aa-devkit simulate`, `aa-devkit eval`, `aa-devkit voice-synth`, `aa-devkit voice-replay`, `aa-devkit latency-report` (`FR-4.1` – `FR-6.4`) |
| **L4: Eval-Driven Hill-Climbing** (`7.8`) | Pre-run Vertex AI token/cost estimator, automated evaluation loss-pattern clustering, and local candidate bundle fix proposals in the customer's project (`PRIV-13`) | `aa-devkit hillclimb`, `pack/skills/aa-eval-hillclimb/` (`FR-7.1` – `FR-7.3`) |

> **Stateless & Zero-Deploy Boundary**: Every `aa-devkit` CLI command operates purely on local JSON directories and `.zip` archives without modifying live Companion Agent configurations. Configuration deployment, Git repository versioning, PR approvals, IAM write grants, and rollbacks belong exclusively to the DevKit user's own Git/CI/CD workflow.

---

## Quick Start

For a complete step-by-step walkthrough with copy-pasteable sample commands and sample outputs across all L1–L4 and Simulator workflows, see **[docs/HOW_TO_USE.md](docs/HOW_TO_USE.md)**.

### 1. Install the CLI locally

```bash
pip install -e ".[dev]"
```

### 2. Install the Skills Pack & Verify Golden Questions (`FR-1.1`, `FR-1.5`)

```bash
# Install for Jetski (.agent/rules + .agent/skills)
aa-devkit install-pack --target agent --dest /path/to/workspace

# Install for Customer Claude Code (.claude/CLAUDE.md + .claude/skills)
aa-devkit install-pack --target claude --dest /path/to/customer-workspace

# Run golden-question regression evaluation across all skills (FR-1.5)
aa-devkit eval-skills
```

### 3. Migrate Legacy AI Coach / PGKA Configs (`FR-1.3`)

```bash
aa-devkit migrate-coach ./legacy_ai_coach_generator.json --output ./migrated_companion_agent.json
```

### 4. Export & Review Configurations (`FR-2.1`, `FR-2.2`)

```bash
# Export full graph to a local folder or .zip archive
aa-devkit export \
  --profile "projects/my-gcp-project/locations/global/conversationProfiles/my-profile-id" \
  --output ./my_agent_bundle.zip

# Run deterministic P0/P1/P2 best-practice review against the included sample bundle
aa-devkit review evals/samples/sample_agent_bundle --fail-on-p0
```

### 5. Import Transcripts, Generate Scenarios & Run 5D Persona Simulations

```bash
# 1. Import Genesys Cloud / Amazon Connect / CCAI / Call Log transcripts & heal [REDACTED] PII
aa-devkit import-transcript evals/samples/sample_genesys_transcript.json \
  --output ./imported_genesys_eval.json

aa-devkit import-transcript evals/samples/sample_call_log.txt \
  --output ./imported_call_log_eval.json

# 2. Generate config-aware synthetic scenarios with Multi-Step Workflow Pacing (1 step = 2 turns)
aa-devkit generate-scenarios \
  --bundle evals/samples/sample_agent_bundle \
  --count 2 \
  --turns 6 \
  --output ./generated_scenarios.json

# 3. Simulate a 5D Customer Persona + 4-Strategy Virtual Human Agent Perturbation (FR-5.5)
aa-devkit simulate \
  --bundle evals/samples/sample_agent_bundle \
  --tone frustrated \
  --tech-literacy low \
  --patience 3 \
  --agent-strategy paraphrase \
  --output ./simulated_5d_session.json
```

### 6. Run Evaluations, Bootstrap Ground Truth, Voice DSP Synthesis & Hill-Climbing (`FR-4.1` – `FR-7.3`)

```bash
# Turn-by-turn evaluation with Layer 0 state-machine checks, Strict/Forgiving F1 & baseline comparison
aa-devkit eval evals/samples/sample_eval_conversations.json \
  --bundle evals/samples/sample_agent_bundle \
  --baseline-run evals/samples/sample_eval_conversations.json

# Bootstrap ground-truth expected_entities & expected_guidance_cards from recorded responses
aa-devkit bootstrap-eval evals/samples/sample_eval_conversations.json \
  --output ./bootstrapped_eval_dataset.json

# Stock-voice TTS audio generation with PSTN telephone filter, call-center noise, ringtone & stereo WAV
aa-devkit voice-synth evals/samples/sample_eval_conversations.json \
  --offline \
  --telephone-filter \
  --noise-profile call_center \
  --stereo \
  --include-transfer-ring \
  --output-dir ./synth_audio

# 6-stage call latency breakdown report or HTML dashboard (FR-6.1 – FR-6.4)
aa-devkit latency-report evals/samples/sample_eval_conversations.json --format html --output ./latency.html

# L4 Hill-Climbing: pre-run cost estimate + loss-pattern analysis + candidate fixes (FR-7.1 – FR-7.3)
aa-devkit hillclimb \
  --bundle evals/samples/sample_agent_bundle \
  --dataset evals/samples/sample_eval_conversations.json \
  --candidate-output ./candidate_bundle/
```

---

## Repository Structure

```text
companion-agent-devkit/
├── cli/aa_devkit/
│   ├── client.py              # Pure REST v2beta1 (Dialogflow) + v1beta (CES) client
│   ├── exporter.py            # Stateless graph exporter to folder or .zip (FR-2.1)
│   ├── reviewer.py            # Deterministic P0/P1/P2 config linter (FR-2.2)
│   ├── migrator.py            # AI Coach & PGKA -> Companion Agent converter (FR-1.3)
│   ├── skill_eval.py          # Golden-question regression suite runner (FR-1.5)
│   ├── transcript_importer.py # 3-Stage Universal Transcript Importer & [REDACTED] PII Healer
│   ├── scenario_generator.py  # Config-Aware Scenario Generator, 5D Persona & 4-Strategy Agent Harness
│   ├── eval_pipeline.py       # Layer 0 state-machine validator, Strict/Forgiving F1 & Bootstrapper (FR-4.1, FR-4.2)
│   ├── voice_tester.py        # Stock-voice TTS, Prosody Normalizer, PSTN Telephony DSP & Replay (FR-5.1–5.3)
│   ├── latency.py             # 6-stage call latency taxonomy & HTML/Markdown reporter (FR-6.1–6.4)
│   ├── hillclimb.py           # Pre-run Vertex AI cost estimator & loss-pattern proposer (FR-7.1–7.3)
│   ├── pack_builder.py        # Single-source compiler for .agent and .claude (FR-1.1)
│   └── main.py                # Stateless CLI entrypoint (`aa-devkit`)
├── evals/
│   ├── golden_questions.json          # Golden regression questions for skills (FR-1.5)
│   ├── schemas/eval_dataset_schema.json
│   └── samples/
│       ├── sample_eval_conversations.json
│       ├── sample_genesys_transcript.json
│       ├── sample_call_log.txt
│       └── sample_agent_bundle/       # Ready-to-run exported Companion Agent bundle
├── pack/                      # Single source of truth for rules, skills, guides & samples
│   ├── rules/companion-agent-guardrails.md
│   └── skills/
│       ├── aa-companion-architect/    # L1 + FR-6.1: Authoring, Decision Guides, Workflow/UI, Latency
│       ├── aa-migration/              # L1 (FR-1.3): AI Coach & PGKA -> Companion Agent migration
│       ├── aa-config-review/          # L2 (FR-2.2): Guided audit workflow for exported bundles
│       └── aa-eval-hillclimb/         # L4 (FR-7.1–7.3): Eval-driven hill-climbing workflow
└── tests/                     # 19 unit & CLI integration tests covering all L1–L4 & Simulator features
```
