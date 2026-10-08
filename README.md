# Agent Assist Companion Agent DevKit (`aa-dev-kit`)

`aa-dev-kit` packages Google Cloud Contact Center AI (CCAI) **Companion Agent** engineering best practices, decision guides, AI Coach / PGKA migration tools, workflow/UI concurrency samples, and stateless configuration export, review, evaluation, voice testing, latency observability, and hill-climbing tools for AI coding agents (Jetski `.agent` and Claude Code `.claude`).

> **Privacy Boundary**: Customer data never goes into this kit. Anything that touches customer data runs in the customer's project or local workspace by default (`PRIV-13`). Third-party AI packages (such as `.claude`) are strictly for customers' own use — Google staff must never load customer data into third-party AI tools.

---

## Architecture & Milestones (L1 – L4)

| Level | Capability | Deliverables & CLI Subcommands |
| :--- | :--- | :--- |
| **L1: Best-Practice Skills Pack** (`7.2`) | Authoring rules, reactive vs. proactive & tool-type decision guides, AI Coach/PGKA migration, Workflow/AnswerRecord APIs, UI concurrency bridge, and golden-question regression suite | `pack/rules/companion-agent-guardrails.md`, `pack/skills/aa-companion-architect/`, `pack/skills/aa-migration/`, `aa-devkit install-pack`, `aa-devkit migrate-coach`, `aa-devkit eval-skills` (`FR-1.1` – `FR-1.5`) |
| **L2: Config Export & Review** (`7.3`) | Stateless export of Conversation Profile + Companion Agent + Workflows + CES/Dialogflow Tools to local folder/zip, plus deterministic & AI-assisted best-practice review | `aa-devkit export`, `aa-devkit review`, `pack/skills/aa-config-review/` (`FR-2.1`, `FR-2.2`) |
| **L3: Eval Pipeline, Voice Testing & Latency** (`7.5` – `7.7`) | Turn-by-turn evaluation pipeline, stock-voice TTS synthesis & streaming replay, and 6-stage call latency taxonomy/dashboards | `aa-devkit eval`, `aa-devkit voice-synth`, `aa-devkit voice-replay`, `aa-devkit latency-report` (`FR-4.1` – `FR-6.4`) |
| **L4: Eval-Driven Hill-Climbing** (`7.8`) | Pre-run Vertex AI token/cost estimator, automated evaluation loss-pattern clustering, and local candidate bundle fix proposals in the customer's project (`PRIV-13`) | `aa-devkit hillclimb`, `pack/skills/aa-eval-hillclimb/` (`FR-7.1` – `FR-7.3`) |

> **Stateless & Zero-Deploy Boundary**: Every `aa-devkit` CLI command operates purely on local JSON directories and `.zip` archives without modifying live Companion Agent configurations. Configuration deployment, Git repository versioning, PR approvals, IAM write grants, and rollbacks belong exclusively to the DevKit user's own Git/CI/CD workflow.

---

## Quick Start

For a complete step-by-step walkthrough across all L1–L4 workflows, see **[docs/HOW_TO_USE.md](docs/HOW_TO_USE.md)**.

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

# Run deterministic P0/P1/P2 best-practice review (use --fail-on-p0 as a CI gate)
aa-devkit review ./my_agent_bundle.zip --fail-on-p0
```

### 5. Run Evaluations, Voice Testing, Latency Reports & Hill-Climbing (`FR-4.1` – `FR-7.3`)

```bash
# Turn-by-turn evaluation pipeline (offline or --live)
aa-devkit eval evals/samples/sample_eval_conversations.json

# Stock-voice synthetic audio generation & configurable streaming replay (FR-5.1 – FR-5.3)
aa-devkit voice-synth evals/samples/sample_eval_conversations.json \
  --project my-customer-gcp-project \
  --output-dir ./synth_audio

# 6-stage call latency breakdown report or HTML dashboard (FR-6.1 – FR-6.4)
aa-devkit latency-report evals/samples/sample_eval_conversations.json --format html --output ./latency.html

# L4 Hill-Climbing: pre-run cost estimate + loss-pattern analysis + candidate fixes (FR-7.1 – FR-7.3)
aa-devkit hillclimb \
  --bundle ./my_agent_bundle/ \
  --dataset evals/samples/sample_eval_conversations.json \
  --candidate-output ./candidate_bundle/
```

---

## Repository Structure

```text
companion-agent-devkit/
├── cli/aa_devkit/
│   ├── client.py          # Pure REST v2beta1 (Dialogflow) + v1beta (CES) client
│   ├── exporter.py        # Stateless graph exporter to folder or .zip (FR-2.1)
│   ├── reviewer.py        # Deterministic P0/P1/P2 config linter (FR-2.2)
│   ├── migrator.py        # AI Coach & PGKA -> Companion Agent converter (FR-1.3)
│   ├── skill_eval.py      # Golden-question regression suite runner (FR-1.5)
│   ├── eval_pipeline.py   # Turn-by-turn evaluation runner & scorer (FR-4.1, FR-4.2)
│   ├── voice_tester.py    # Stock-voice TTS synthesizer & configurable streaming replay (FR-5.1–5.3)
│   ├── latency.py         # 6-stage call latency taxonomy & HTML/Markdown reporter (FR-6.1–6.4)
│   ├── hillclimb.py       # Pre-run Vertex AI cost estimator & loss-pattern proposer (FR-7.1–7.3)
│   ├── pack_builder.py    # Single-source compiler for .agent and .claude (FR-1.1)
│   └── main.py            # Stateless CLI entrypoint (`aa-devkit`)
├── evals/
│   ├── golden_questions.json          # Golden regression questions for skills (FR-1.5)
│   ├── schemas/eval_dataset_schema.json
│   └── samples/sample_eval_conversations.json
├── pack/                  # Single source of truth for rules, skills, guides & samples
│   ├── rules/companion-agent-guardrails.md
│   └── skills/
│       ├── aa-companion-architect/    # L1 + FR-6.1: Authoring, Decision Guides, Workflow/UI, Latency
│       ├── aa-migration/              # L1 (FR-1.3): AI Coach & PGKA -> Companion Agent migration
│       ├── aa-config-review/          # L2 (FR-2.2): Guided audit workflow for exported bundles
│       └── aa-eval-hillclimb/         # L4 (FR-7.1–7.3): Eval-driven hill-climbing workflow
└── tests/                 # Unit tests covering all L1–L4 modules
```
