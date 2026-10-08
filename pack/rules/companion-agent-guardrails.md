---
trigger: always_on
description: Mandatory engineering guardrails and privacy rules when authoring, reviewing, or troubleshooting Google Cloud Agent Assist Companion Agents, Workflows, and CES Tools.
---

# Companion Agent Authoring & Privacy Guardrails

Whenever you create, edit, review, or troubleshoot a Google Cloud **Companion Agent**, **Conversation Profile**, **CompanionAgentWorkflow**, or **CES / Dialogflow Tool**, enforce these non-negotiable invariants:

1. **Privacy & Data Boundary (FR-1.1)**:
   - Never hard-code or commit real customer PII, transcripts, or audio into the DevKit repository or reusable templates.
   - Third-party AI packages (such as `.claude`) are strictly for customers' own use within their own environment. Google staff must **never** load customer data into third-party AI tools.
2. **`displayDetails` is UI-Only (Never Sent to the LLM)**:
   - The Companion Agent backend renders `displayDetails` only in the human agent's UI card header (`<instruction>` XML sent to the LLM omits `displayDetails`).
   - Keep `displayDetails` to a 1-sentence human summary. Put all trigger rules, exclusions, and `{@TOOL:...}` bindings inside `condition`, `actions[].description`, or `overarchingGuidance`.
3. **Tool Binding Syntax (`{@TOOL:tool_name}`)**:
   - Always bind tools using `{@TOOL:tool_name}` (or full CES path `{@TOOL:projects/.../apps/.../tools/...}`) and verify the tool is linked in `cesToolSpecs`.
   - **Never** use legacy `{$tool.tool_name}` syntax — it does not bind or execute tools in Companion Agent.
4. **Multi-Step `actions[]` Checkpoint Rule**:
   - Never isolate negative constraints (`"Do NOT ask for SSN"`) or formatting rules (`"Keep response under 2 sentences"`) in their own standalone `actions[]` step. Because "not doing something" produces no observable transcript event, `completed_actions` permanently stalls on that step and blocks subsequent tool calls. Always fold negative/brevity constraints into the positive action step they modify.
5. **One Scenario = One Card (No `IF / ELSE` Branching)**:
   - Never write conditional sub-branches (`"If X do Y; otherwise do Z"`) inside a single `GuidanceInstruction` card. Split mutually exclusive scenarios into separate cards with mutually exclusive `condition` definitions.
6. **Positive Conversational Intent & `triggerEvent` Overrides**:
   - Never use negative trigger phrasing (`"Do NOT suggest during initial greetings"`) in `condition` or Workflow `description`.
   - Set `"triggerEvent": "CUSTOMER_MESSAGE"` on customer-driven intake/verification cards so they do not fire prematurely on the human agent's Turn-1 opening greeting when the skill uses `END_OF_UTTERANCE`.
7. **Preserve Existing `skillConfigs` on Update**:
   - Dialogflow `PATCH` replaces the `skillConfigs` array. When modifying an existing Companion Agent, always preserve existing `guidanceInstructions`, `cesToolSpecs`, and `companionAgentWorkflowSpecs` unless explicitly asked to remove them.
