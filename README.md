# Agent Assist Companion Agent DevKit (`aa-dev-kit`)

`aa-dev-kit` packages Google Cloud Contact Center AI (CCAI) **Companion Agent** engineering best practices, decision guides, workflow/UI concurrency samples, and stateless configuration export & review tools for AI coding agents (Jetski `.agent` and Claude Code `.claude`).

> **Privacy Boundary**: Customer data never goes into this kit. Anything that touches customer data runs in the customer's project or local workspace by default. Third-party AI packages (such as `.claude`) are strictly for customers' own use — Google staff must never load customer data into third-party AI tools.

---

## Architecture & Milestones

| Level | Capability | Deliverables |
| :--- | :--- | :--- |
| **L1: Best-Practice Skills Pack** | General authoring guidance, decision matrices, and Workflow API / UI concurrency samples (zero project access required) | `pack/rules/companion-agent-guardrails.md`, `pack/skills/aa-companion-architect/`, and `aa-devkit install-pack` (FR-1.1, FR-1.2, FR-1.4) |
| **L2: Config Export & Review** | Stateless export of Conversation Profile + Companion Agent + Workflows + CES/Dialogflow Tools to local folder/zip, plus deterministic & AI-assisted best-practice review | `aa-devkit export`, `aa-devkit review`, and `pack/skills/aa-config-review/` (FR-2.1, FR-2.2) |

> **Stateless Design**: `aa-devkit` CLI commands (`export`, `review`) operate purely on local JSON directories and `.zip` archives. Git repository management, versioning, change approvals, and rollbacks are the responsibility of the DevKit user's own Git/CI workflow.

---

## Quick Start

For a complete step-by-step walkthrough (including example AI prompts, `.agent` vs. `.claude` setup, and `.zip` vs. live GCP export flows), see **[docs/HOW_TO_USE.md](docs/HOW_TO_USE.md)**.

### 1. Install the CLI locally

```bash
pip install -e ".[dev]"
```

### 2. Install the L1/L2 Skills Pack into your AI Coding Agent (FR-1.1)

Compile the single-source `pack/` into `.agent/` (for Jetski) or `.claude/` (for customer Claude Code environments):

```bash
# Install for Jetski (.agent/rules + .agent/skills)
aa-devkit install-pack --target agent --dest /path/to/workspace

# Install for Customer Claude Code (.claude/CLAUDE.md + .claude/skills)
aa-devkit install-pack --target claude --dest /path/to/customer-workspace
```

### 3. Export a Companion Agent Configuration Bundle (FR-2.1)

Export a `ConversationProfile` or `CompanionAgent` (along with all linked `CompanionAgentWorkflows`, CES `v1beta` Tools, and Dialogflow `v2beta1` Tools) into a local folder or `.zip` archive:

```bash
# Export to a .zip archive (zip route first)
aa-devkit export \
  --profile "projects/my-gcp-project/locations/global/conversationProfiles/my-profile-id" \
  --env prod \
  --output ./my_agent_bundle.zip

# Or export directly by CompanionAgent resource name into a local folder
aa-devkit export \
  --agent "projects/my-gcp-project/locations/global/companionAgents/my-agent-id" \
  --env prod \
  --output ./my_agent_bundle/
```

### 4. Run Best-Practice Config Review (FR-2.2)

Audit an exported directory or `.zip` bundle against the engineering best-practice rules (returning prioritized **P0 / P1 / P2** findings):

```bash
# Output Markdown report
aa-devkit review ./my_agent_bundle.zip

# Output structured JSON report
aa-devkit review ./my_agent_bundle.zip --format json
```

---

## Repository Structure

```text
companion-agent-devkit/
├── cli/aa_devkit/
│   ├── client.py          # Pure REST v2beta1 (Dialogflow) + v1beta (CES) client
│   ├── exporter.py        # Stateless graph exporter to folder or .zip (FR-2.1)
│   ├── reviewer.py        # Deterministic P0/P1/P2 config linter (FR-2.2)
│   ├── pack_builder.py    # Single-source compiler for .agent and .claude (FR-1.1)
│   └── main.py            # Stateless CLI entrypoint (`aa-devkit`)
├── pack/                  # Single source of truth for rules, skills, guides & samples
│   ├── rules/
│   │   └── companion-agent-guardrails.md
│   └── skills/
│       ├── aa-companion-architect/   # L1: Authoring rules, Decision Guides (FR-1.2), Workflow/UI APIs (FR-1.4)
│       └── aa-config-review/         # L2: Guided audit workflow for exported bundles (FR-2.2)
└── tests/                 # Unit tests & synthetic fixtures
```
