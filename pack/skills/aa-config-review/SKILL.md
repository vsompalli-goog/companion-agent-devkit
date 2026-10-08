---
name: aa-config-review
description: Export and audit a Google Cloud Agent Assist Companion Agent configuration (Conversation Profile, Companion Agent, Workflows, and CES/Dialogflow Tools) from a local folder, .zip archive, or GCP project against engineering best practices. Use when asked to export an agent config, review an exported bundle or zip, check datastore rewriter or summarization settings, or recommend prioritized P0/P1/P2 improvements.
---

# Companion Agent Configuration Export & Review (L2)

Use this skill to export a Companion Agent configuration graph (`FR-2.1`) or audit an exported local folder / `.zip` bundle (`FR-2.2`) and produce specific, prioritized (**P0 / P1 / P2**) recommendations.

> **Privacy & Access Reminder**:
> - In the **zip route** (default for customer self-serve), the customer drops an exported `.zip` or folder (`conversation_profile.json`, `companion_agent.json`, `workflows/`, `tools/`) into their workspace.
> - Google staff must **never** load customer data into third-party AI tools (such as `.claude`).

---

## Step 1: Obtain or Export the Configuration Bundle (`FR-2.1`)

If the user has already provided a local directory or `.zip` file, proceed directly to **Step 2**.

If the user provides a GCP `ConversationProfile` or `CompanionAgent` resource path and has read access configured via Application Default Credentials (`gcloud auth application-default login`), run the stateless exporter:

```bash
# Export by ConversationProfile into a .zip bundle
aa-devkit export \
  --profile "projects/<project_id>/locations/<location>/conversationProfiles/<profile_id>" \
  --env prod \
  --output ./companion_agent_export.zip
```

---

## Step 2: Run the Deterministic Best-Practice Linter (`FR-2.2`)

Always run the deterministic reviewer first to surface structural and syntactic violations:

```bash
aa-devkit review <path_to_folder_or_zip>
```

---

## Step 3: Deep Semantic Audit & Prioritized Report

After running `aa-devkit review`, inspect the bundle files (`conversation_profile.json`, `companion_agent.json`, `workflows/*.json`, `tools/**/*.json`) against [references/review_checklist.md](references/review_checklist.md) to catch semantic nuances that static regex checks cannot fully evaluate:

1. **P0 — Critical Blockers (Breaks Runtime Execution or Tool Binding)**:
   - Legacy `{$tool.*}` syntax or unlinked `{@TOOL:*}` tools (`CA-P0-001`).
   - Trigger logic or tool rules hidden inside UI-only `displayDetails` (`CA-P0-002`).
   - Standalone negative (`Do NOT...`) or formatting (`Keep under 2 sentences`) action steps that stall `completed_actions` (`CA-P0-003`).
   - CES Python tool `pythonFunction.name` vs. `def <name>(...)` mismatch (`CA-P0-004`).

2. **P1 — High-Priority Reliability & Quality Fixes**:
   - `IF / ELSE` branching inside a single `GuidanceInstruction` card (`CA-P1-001`).
   - Negative trigger words (`"Do NOT suggest during greetings"`) or missing `"triggerEvent": "CUSTOMER_MESSAGE"` on intake/verification cards (`CA-P1-002`).
   - `<call_context>` conditions missing Pattern A / Pattern B (`DEFAULT` + `EXCEPTION`) structure, or containing attractor phrases (`CA-P1-003`).
   - `ConversationProfile` missing `CONVERSATION_SUMMARIZATION`, missing Datastore / Knowledge Assist `queryConfig` & rewriter settings, or broad Guidance conditions starving Knowledge Assist (`CA-P1-004`).
   - State-modifying tools configured with `confirmationRequirement: "NOT_REQUIRED"` (`CA-P1-005`).

3. **P2 — Hygiene & Maintainability**:
   - `overarchingGuidance` 4-section structure (`CA-P2-001`) and voice `sttConfig` (`useLongFormModel`, `useGeminiAsr`).

Present your final output grouped by **P0**, **P1**, and **P2**, citing exact resource names/cards and providing copy-ready JSON snippets for each recommended fix.
