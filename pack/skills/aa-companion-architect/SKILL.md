---
name: aa-companion-architect
description: Design, author, and troubleshoot Google Cloud Agent Assist Companion Agents, Guidance Instructions, Workflows, CES/Dialogflow Tools, Answer Record APIs, and UI concurrency bridges. Use when choosing between reactive vs. proactive modes, selecting tool types (MCP, Python, OpenAPI, Datastore), authoring prompts/configs, or integrating Workflow Agent APIs with <agent-assist-companion-agent>.
---

# Agent Assist Companion Agent Architect (L1)

Use this skill whenever designing, authoring, or troubleshooting a Google Cloud Contact Center AI (CCAI) **Companion Agent**, **GuidanceSkillConfig**, **CompanionAgentWorkflow**, **CES Tool**, or **UI Module integration**.

## Progressive Disclosure Reference Map

Read only the reference file(s) needed for your immediate task:

1. **Architectural Decision Guides (`FR-1.2`)**:
   - Read [references/decision_guides.md](references/decision_guides.md) when choosing between:
     - **Reactive Query** (`textInput.companionQuery` / `:streamCompanionReactiveQuestion`) vs. **Proactive Suggestion** (`END_OF_UTTERANCE` / `CUSTOMER_MESSAGE`)
     - **Tool Types**: **MCP Tool** vs. **CES Python Tool** (`pythonFunction`) vs. **OpenAPI Tool** (`openApiTool` / `openApiSpec`) vs. **Datastore Tool** (Vertex AI Search / Agentic RAG Knowledge Assist)
     - **Instruction Agent** (`GuidanceSkillConfig`) vs. **Workflow Agent** (`CompanionAgentWorkflow`)

2. **Backend Authoring Best Practices (`FR-1.1`)**:
   - Read [references/authoring_best_practices.md](references/authoring_best_practices.md) when writing or debugging `overarchingGuidance`, `guidanceInstructions`, `<call_context>` conditions, multi-step `actions[]`, or CES Python Function tools.
   - Inspect [examples/guidance_cards_good_vs_bad.json](examples/guidance_cards_good_vs_bad.json) for side-by-side anti-patterns vs. production-ready JSON patterns.

3. **Workflow Agent APIs, Answer Record Updates & UI Concurrency (`FR-1.4`)**:
   - Read [references/workflow_api_and_ui_concurrency.md](references/workflow_api_and_ui_concurrency.md) when implementing:
     - `POST /v2beta1/{participant}:analyzeContent` workflow transitions (`companionSuggestionInput.workflowState` for start, step completion, revert, variable edits, exit)
     - Answer Record tool confirmation/cancellation (`suggestionInput`) and feedback updates (`PATCH /v2beta1/{answerRecord}?updateMask=answer_feedback`)
     - Concurrency & race handling on the UI side (`<agent-assist-companion-agent>` + `UiModulesConnector`)
   - Reference [examples/workflow_api_samples.py](examples/workflow_api_samples.py) for pure REST Python client calls and [examples/ui_concurrency_bridge_sample.js](examples/ui_concurrency_bridge_sample.js) for UI Module deduplication and fallback handling.

4. **Latency Breakdown & Observability Taxonomy (`FR-6.1` – `FR-6.4`)**:
   - Read [references/latency_taxonomy.md](references/latency_taxonomy.md) when classifying Companion Agent call latency across the 6 canonical spans (client-side integration/UI vs. backend STT/LLM/tool/quota metrics) or interpreting `aa-devkit latency-report` outputs.

## Quick Authoring Checklist

Before delivering any Companion Agent JSON or prompt configuration, verify:
- [ ] `displayDetails` contains only a short 1-sentence UI summary (zero LLM instructions or `{@TOOL:...}` tags).
- [ ] Every tool binding uses `{@TOOL:tool_name}` (never `{$tool.tool_name}`) and is linked in `cesToolSpecs`.
- [ ] Multi-step `actions[]` split **only** at observable conversational or tool checkpoints (no standalone `Do NOT...` or brevity action steps).
- [ ] Each `GuidanceInstruction` card handles a single scenario (no `IF / ELSE` branching inside one card).
- [ ] Intake/verification cards set `"triggerEvent": "CUSTOMER_MESSAGE"` when the parent skill uses `END_OF_UTTERANCE`.
- [ ] Context-gated cards use **Pattern A** (1-sentence zero-overlap when ingestion is guaranteed) or **Pattern B** (`DEFAULT` + `EXCEPTION` fail-safe when context may be empty/late, with no attractor phrases in Exception actions).
- [ ] CES Python tools have exact `pythonFunction.name` == `def <name>(...)` parity, type hints, Google-style `Args:`/`Returns:` docstrings, and `confirmationRequirement: "REQUIRED"` for mutations.
