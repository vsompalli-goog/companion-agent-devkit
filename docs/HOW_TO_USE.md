# How to Use the Agent Assist Companion Agent DevKit (`aa-dev-kit`)

This guide walks **Google Forward Deployed Engineers (FDEs)** and **Customer Engineers (CEs)** step-by-step through installing and using `aa-dev-kit` for **Milestone L1 (Best-Practice Skills Pack)** and **Milestone L2 (Stateless Config Export & Review)**.

---

## 1. Prerequisites & Installation

### Requirements
- **Python**: `3.10+`
- **GCP Authentication** *(only required when running `aa-devkit export` against a live GCP project)*:
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

> **Stateless Guarantee**: Every `aa-devkit` command operates strictly on local JSON folders or `.zip` files. Git repository initialization, commits, branch management, approvals, and rollbacks remain completely under your own Git/CI workflow.

---

## 2. Workflow 1: Install the Skills Pack into Your AI Coding Agent (L1 — `FR-1.1`)

`aa-devkit install-pack` compiles the single-source `pack/` directory into native customization folders for your AI coding agent.

### Option A: For Google FDEs & Engineers Using Jetski (`.agent`)
Run from your target workspace (or pass `--dest /path/to/workspace`):

```bash
aa-devkit install-pack --target agent --dest /path/to/your/workspace
```

**What this installs into `/path/to/your/workspace/.agent/`**:
- `.agent/rules/companion-agent-guardrails.md` — Always-on authoring and privacy guardrails automatically applied by Jetski whenever you work on Companion Agent files.
- `.agent/skills/aa-companion-architect/` — L1 skill covering the 9 backend authoring rules, Decision Guides (`FR-1.2`), and Workflow API / UI concurrency samples (`FR-1.4`).
- `.agent/skills/aa-config-review/` — L2 skill for auditing exported configuration bundles (`FR-2.2`).

### Option B: For Customer Engineers Using Claude Code (`.claude`)
Customers working inside their own environment can generate a `.claude` customization bundle:

```bash
aa-devkit install-pack --target claude --dest /path/to/customer/workspace
```

**What this installs into `/path/to/customer/workspace/.claude/`**:
- `.claude/CLAUDE.md` — Always-on rules compiled with the mandatory customer-only privacy notice.
- `.claude/skills/aa-companion-architect/` & `.claude/skills/aa-config-review/`.

> **Privacy Rule (`FR-1.1`)**: Third-party AI packages (such as `.claude`) are strictly for customers' own use inside their own environment. Google staff must **never** load customer data into third-party AI tools.

---

## 3. Workflow 2: Using L1 Skills for Agent Design & Troubleshooting (`FR-1.1`, `FR-1.2`, `FR-1.4`)

Once `install-pack` has been run in your workspace, your AI coding agent automatically discovers `aa-companion-architect`. You do not need to paste best-practice docs or API specs into chat.

### Common Tasks & Example Prompts

#### A. Architecture & Tool Type Decisions (`FR-1.2`)
Ask your coding agent:
- *"Should I model our fee-waiver policy as a proactive GuidanceInstruction card, a CompanionAgentWorkflow, or rely on reactive 'Ask Assistant' queries?"*
- *"We have an internal REST billing API, a policy PDF repository, and an eligibility calculation that combines three fields. Which Companion Agent tool types (MCP, CES Python, OpenAPI, or Datastore) should we use for each?"*

#### B. Authoring Context-Gated Guidance Cards & CES Python Tools (`FR-1.1`)
Ask your coding agent:
- *"Draft a pair of Companion Agent GuidanceInstruction cards for caller identity verification where `customerContext` (`ivr_authenticated`) might arrive late or be missing. Follow Pattern B (`DEFAULT` + `EXCEPTION`) and use `{@TOOL:lookup_customer_account}`."*
- *"Write a CES Python Function tool `calculate_prorated_credit` with proper type hints, Google-style docstring (`Args:`/`Returns:`), and `cesToolSpecs` configuration."*

#### C. Integrating Workflow Agent APIs & UI Concurrency (`FR-1.4`)
Ask your coding agent:
- *"Show me the exact `v2beta1` REST `:analyzeContent` payload to mark step `verify_identity` as `DONE` (`USER_COMPLETED`) in a CompanionAgentWorkflow."*
- *"How do I confirm a `NEEDS_CONFIRMATION` tool call using `suggestionInput.answerRecord` and patch `AnswerRecord` feedback via REST?"*
- *"Our custom desktop embeds `<agent-assist-companion-agent>` and `UiModulesConnector`, and clicking Confirm on a tool fires duplicate `:analyzeContent` calls. Show me how to deduplicate `answerRecord` tool confirmations and handle connector timeouts."*

*(You can also inspect the standalone reference implementations directly in `pack/skills/aa-companion-architect/examples/workflow_api_samples.py` and `pack/skills/aa-companion-architect/examples/ui_concurrency_bridge_sample.js`.)*

---

## 4. Workflow 3: Export a Companion Agent Configuration Bundle (L2 — `FR-2.1`)

`aa-devkit export` deep-harvests the entire configuration graph via pure REST (`Dialogflow v2beta1` + `CES v1beta`) so preview fields (`companionAgents`, `companionAgentWorkflows`, `cesToolSpecs`, `pythonFunction`) are preserved intact.

### Option A: Export to a `.zip` Archive ("Zip Route First")
Recommended for customer self-serve exports so a single `.zip` file can be dropped into a review workspace:

```bash
aa-devkit export \
  --profile "projects/my-gcp-project/locations/global/conversationProfiles/my-profile-id" \
  --env prod \
  --output ./companion_agent_bundle.zip
```

### Option B: Export Directly by `CompanionAgent` Resource into a Folder
If you are reviewing a `CompanionAgent` before it is linked to a `ConversationProfile` (or in `staging`):

```bash
aa-devkit export \
  --agent "projects/my-gcp-project/locations/us-central1/companionAgents/my-agent-id" \
  --env staging \
  --quota-project my-billing-project \
  --output ./companion_agent_bundle/
```

### Exported Bundle Structure
Whether exported as a folder or `.zip`, the bundle contains:
```text
companion_agent_bundle/ (or .zip)
├── manifest.json                  # Export metadata (timestamp, env, endpoints, resource counts)
├── conversation_profile.json      # v2beta1 ConversationProfile (features, STT, Knowledge Assist)
├── companion_agent.json           # v2beta1 CompanionAgent (overarchingGuidance, skillConfigs, cesToolSpecs)
├── workflows/
│   └── <workflow_id>.json         # All linked v2beta1 CompanionAgentWorkflows and step graphs
└── tools/
    ├── ces/
    │   └── <tool_id>.json         # CES v1beta tools (pythonFunction code, openApiTool schemas)
    └── dialogflow/
        └── <tool_id>.json         # Dialogflow v2beta1 tools (functionSpec, openApiSpec)
```

---

## 5. Workflow 4: Run a Best-Practice Configuration Review (L2 — `FR-2.2`)

You can audit an exported folder or `.zip` bundle either via the deterministic CLI or interactively inside your AI coding agent using the `aa-config-review` skill.

### Step 1: Run the Deterministic CLI Reviewer
```bash
# Print prioritized Markdown report (P0 / P1 / P2) to stdout
aa-devkit review ./companion_agent_bundle.zip

# Save Markdown report to a file
aa-devkit review ./companion_agent_bundle.zip --output ./review_report.md

# Output machine-readable JSON (e.g. for CI pipelines) and return exit code 1 if any P0 blocker exists
aa-devkit review ./companion_agent_bundle.zip --format json --fail-on-p0
```

### Step 2: Run an Interactive AI-Assisted Deep Review (`aa-config-review`)
Drop `./companion_agent_bundle.zip` into your workspace and prompt your AI coding agent:
> *"Review `./companion_agent_bundle.zip` using `aa-config-review`. Run `aa-devkit review`, inspect the guidance cards, workflows, tools, and conversation profile settings, and give me prioritized P0/P1/P2 recommendations with copy-ready JSON fixes."*

### Reference: Deterministic Review Rules (`CA-P0-001` – `CA-P2-001`)

| Rule ID | Severity | Target | What It Checks |
| :--- | :--- | :--- | :--- |
| **`CA-P0-001`** | **P0** | `CompanionAgent` | Legacy `{$tool.*}` syntax or `{@TOOL:*}` bindings missing from `cesToolSpecs` / `tools`. |
| **`CA-P0-002`** | **P0** | `CompanionAgent` | Trigger conditions, exclusions, or `{@TOOL:*}` tags hidden inside UI-only `displayDetails` (never sent to the LLM). |
| **`CA-P0-003`** | **P0** | `CompanionAgent` | Standalone negative (`Do NOT...`) or formatting (`Keep under 2 sentences`) steps in `actions[]` that permanently stall `completed_actions`. |
| **`CA-P0-004`** | **P0 / P1** | `CES Python Tool` | `pythonFunction.name` vs. `def <name>(...)` mismatch (**P0**), or missing parameter type hints / Google-style `Args:` & `Returns:` docstrings (**P1**). |
| **`CA-P1-001`** | **P1** | `CompanionAgent` | `IF / ELSE` or `Otherwise` conditional branching inside a single `GuidanceInstruction` card (*violates One Scenario = One Card*). |
| **`CA-P1-002`** | **P1** | `Agent` / `Workflow` | Negative trigger phrasing (`Do NOT suggest during greetings`) or intake/verification cards missing `"triggerEvent": "CUSTOMER_MESSAGE"` under `END_OF_UTTERANCE`. |
| **`CA-P1-003`** | **P1** | `CompanionAgent` | `<call_context>` conditions gating on absence without Pattern B (`DEFAULT` + `EXCEPTION`) or containing attractor phrases in Exception actions. |
| **`CA-P1-004`** | **P1 / P2** | `ConversationProfile` | Missing `CONVERSATION_SUMMARIZATION` feature config, missing `KNOWLEDGE_ASSIST` `queryConfig` / datastore rewriter settings, or suboptimal voice `sttConfig`. |
| **`CA-P1-005`** | **P1** | `CES / DF Tool` | State-modifying tools (`cancel_*`, `refund_*`, `update_*`, `create_*`, `waive_*`) configured without `confirmationRequirement: "REQUIRED"`. |
| **`CA-P2-001`** | **P2** | `CompanionAgent` | `overarchingGuidance` missing the recommended 4-section structure (Role & Persona, Channel Style & Brevity, Priority Hierarchy, Global Completion & Guardrails). |
