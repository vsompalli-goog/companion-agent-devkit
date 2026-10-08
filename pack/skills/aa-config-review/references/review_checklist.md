# Companion Agent Configuration Review Checklist (FR-2.2)

Use this checklist alongside `aa-devkit review <bundle>` to evaluate exported Companion Agent bundles across all four layers: **Conversation Profile**, **Companion Agent Guidance**, **Workflows**, and **Tools**.

---

## 1. Conversation Profile (`conversation_profile.json`)
- **Companion Agent Linkage**: Verify `humanAgentAssistantConfig.humanAgentSuggestionConfig.companionAgent` points to the expected `CompanionAgent` resource path.
- **Conversation Summarization (`CA-P1-004`)**:
  - Verify `CONVERSATION_SUMMARIZATION` (or `CONVERSATION_SUMMARIZATION_VOICE`) is enabled in `featureConfigs` with a valid generator/model so `<agent-assist-companion-agent>` renders Handoff and Wrap-Up summaries (`sortedTextSections` + `answerRecord`).
- **Datastore Rewriter & Knowledge Assist (`CA-P1-004`)**:
  - When `KNOWLEDGE_ASSIST` / `AGENTIC_KNOWLEDGE_ASSIST` is configured, verify `queryConfig` and datastore query rewriter settings are populated.
  - Check whether overly broad `GuidanceInstruction` conditions in `companion_agent.json` will fire on every turn and suppress Knowledge Assist answers.
- **Voice STT Settings (`CA-P1-004`)**:
  - For voice channels, verify `sttConfig` enables `useLongFormModel: true` and `useGeminiAsr: true`.

---

## 2. Companion Agent Guidance (`companion_agent.json`)
- **`displayDetails` Visibility (`CA-P0-002`)**:
  - Confirm `displayDetails` is a short 1-sentence UI summary and contains **zero** LLM instructions, trigger conditions, or `{@TOOL:...}` bindings.
- **Tool Binding Syntax & Preconditions (`CA-P0-001`)**:
  - Confirm all tool invocations use `{@TOOL:tool_name}` (never `{$tool.tool_name}`) and that every referenced tool is registered in `cesToolSpecs`.
- **Multi-Step `actions[]` Checkpoint Rule (`CA-P0-003`)**:
  - Confirm every step in `actions[]` corresponds to an observable conversational turn or tool execution checkpoint.
  - Ensure no standalone negative (`"Do NOT..."`) or formatting (`"Keep under 2 sentences"`) action steps exist.
- **One Scenario = One Card (`CA-P1-001`)**:
  - Split any card containing `IF ... ELSE` or `Otherwise` branches into separate mutually exclusive `GuidanceInstruction` cards.
- **Positive Trigger Phrasing & `triggerEvent` (`CA-P1-002`)**:
  - Remove negative trigger phrasing (`"Do NOT suggest during greetings"`).
  - Verify customer-driven intake/verification cards set `"triggerEvent": "CUSTOMER_MESSAGE"` when `skillTriggeringEvent` is `END_OF_UTTERANCE`.
- **`<call_context>` Gating (`CA-P1-003`)**:
  - Verify context-dependent cards use **Pattern A** (1-sentence zero-overlap when guaranteed) or **Pattern B** (`DEFAULT` + `EXCEPTION` fail-safe when context can be missing/late), with zero attractor phrases in Exception actions.
- **`overarchingGuidance` 4-Section Structure (`CA-P2-001`)**:
  - Check for concise coverage of: (1) Role & Persona, (2) Channel Style & Brevity, (3) Priority Hierarchy, (4) Global Completion & Guardrails.

---

## 3. Workflows (`workflows/*.json`)
- **Entry Description (`CA-P1-002`)**: Verify `description` uses positive customer-intent phrasing without negative scoping.
- **Step Types & Completion Semantics**:
  - `informationCollectionAction` (`STEP_TYPE_COLLECT`): Verify `variables` lists all required slots so the step completes only after customer input.
  - `informationalResponseAction` (`STEP_TYPE_INFORM`): Check `verbatim` flag usage (only set `verbatim: true` when strict regulatory/compliance phrasing is required).
  - `systemAction.cesToolSpec` (`STEP_TYPE_TOOL_CALL`): Verify `outputVariableMappings` descriptions clearly explain how to extract fields for downstream transitions.

---

## 4. CES & Dialogflow Tools (`tools/**/*.json`)
- **CES Python Function Parity (`CA-P0-004`)**:
  - Verify `pythonFunction.name` matches `def <name>(...)` in `pythonCode` character-for-character.
  - Verify parameter type hints and Google-style `Args:` / `Returns:` docstrings are present.
- **Mutation Confirmation (`CA-P1-005`)**:
  - Verify state-modifying tools (`cancel_*`, `refund_*`, `update_*`, `create_*`, `waive_*`) set `"confirmationRequirement": "REQUIRED"`.
