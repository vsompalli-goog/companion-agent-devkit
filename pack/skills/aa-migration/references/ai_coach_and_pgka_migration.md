# AI Coach & PGKA $\rightarrow$ Companion Agent Migration Guide (FR-1.3)

Legacy Agent Assist features (**AI Coach** and **PGKA / GKA**) are consolidated into a single **Companion Agent** experience. This guide maps legacy concepts to Companion Agent, provides step-by-step conversion instructions, and documents critical runtime behavioral differences.

---

## 1. AI Coach (`agentCoachingContext`) $\rightarrow$ Companion Agent Concept Mapping

| Legacy AI Coach (`v2beta1/Generator`) | Companion Agent (`v2beta1/CompanionAgent`) | Critical Migration Change |
| :--- | :--- | :--- |
| `Generator.agentCoachingContext` | `CompanionAgent.skillConfigs[].guidanceSkillConfig` | Top-level resource changes from `generators/*` to `companionAgents/*` linked on `ConversationProfile.humanAgentAssistantConfig.humanAgentSuggestionConfig.companionAgent`. |
| `overarchingGuidance` | `guidanceSkillConfig.overarchingGuidance` | Structure into 4 sections: Role & Persona, Channel Style & Brevity, Priority Hierarchy, Global Completion & Guardrails. |
| `instructions[].displayName` | `guidanceInstructions[].displayName` | Keep concise (3–6 words) with positive intent. |
| `instructions[].displayDetails` | `guidanceInstructions[].displayDetails` | **CRITICAL**: In AI Coach, `displayDetails` was often included in prompt context. In Companion Agent, **`displayDetails` is UI-only and NEVER sent to the LLM**. Move any rules out of `displayDetails` into `condition` or `actions[]`. |
| `instructions[].condition` | `guidanceInstructions[].condition` | Must state positive observable customer intent or exact `<call_context>` state (Pattern A or Pattern B `DEFAULT`/`EXCEPTION`). |
| `instructions[].agentAction` + `systemAction` | `guidanceInstructions[].actions[].description` | Ordered multi-step `actions[]` array tracked via `completed_actions` across turns. Split only at observable checkpoints (**Checkpoint Rule**). |
| `{$tool.tool_name}` & `Generator.tools[]` | `{@TOOL:tool_name}` & `CompanionAgent.cesToolSpecs[]` | Legacy `{$tool.tool_name}` **will not bind**. Must rewrite to `{@TOOL:tool_name}` and register in `cesToolSpecs` with `proactiveEnabled` / `reactiveEnabled` and `confirmationRequirement`. |
| `Generator.triggerEvent` | `skillTriggeringEvent` + per-card `triggerEvent` | Companion Agent supports per-card `"triggerEvent": "CUSTOMER_MESSAGE"` overrides so intake cards don't misfire on the human agent's Turn-1 greeting under `END_OF_UTTERANCE`. |

---

## 2. Conversion Steps: AI Coach $\rightarrow$ Companion Agent

1. **Run Stateless Schema Migration**:
   ```bash
   aa-devkit migrate-coach ./legacy_generator.json --output ./migrated_agent.json
   ```
2. **Rescue Hidden Logic from `displayDetails`**:
   - Inspect every migrated card's `displayDetails`. Ensure it is a 1-sentence UI summary and that all conditions/rules live in `condition` or `actions[].description`.
3. **Refactor Monolithic Actions into Checkpoint-Aligned `actions[]`**:
   - If a legacy instruction combined *"Ask for Account ID and then call lookup tool"*, split it into:
     - `actions[0]`: Collect missing verification fields from the customer.
     - `actions[1]`: Once confirmed in transcript, invoke `{@TOOL:lookup_account}` and summarize.
   - Fold any standalone negative rules (`"Do NOT ask for SSN"`) into the positive action step they modify.
4. **Split Multi-Branch Cards (`One Scenario = One Card`)**:
   - If a legacy instruction had `"If VIP do X; otherwise do Y"`, split it into two separate `GuidanceInstruction` cards with mutually exclusive `condition` fields.
5. **Migrate Tools to CES (`v1beta`) or `cesToolSpecs`**:
   - Convert legacy `functionSpec` / `openApiSpec` tools into CES Tools (`pythonFunction` or `openApiTool`) or link existing tools in `cesToolSpecs` with appropriate `confirmationRequirement` (`REQUIRED` for mutations, `NOT_REQUIRED` for lookups).

---

## 3. PGKA / GKA $\rightarrow$ Companion Agent Migration

When migrating customers from standalone **Proactive Generative Knowledge Assist (PGKA / GKA)** to Companion Agent:

1. **How PGKA Maps into Companion Agent**:
   - Unstructured knowledge retrieval (Vertex AI Search datastores, FAQ documents, SOP manuals) continues to be configured on `ConversationProfile` (`KNOWLEDGE_ASSIST` / `AGENTIC_KNOWLEDGE_ASSIST` with `queryConfig` and datastore query rewriter settings), while structured procedural coaching moves into `CompanionAgent` Guidance cards or Workflows.
   - Reactive queries ("Ask Assistant" via `:streamCompanionReactiveQuestion`) replace standalone GKA search boxes inside `<agent-assist-companion-agent>`.
2. **Critical Engine Interaction (Guidance Suppresses Knowledge Assist)**:
   - In Companion Agent, Guidance Instructions, Workflow Selection, and Knowledge Assist run in parallel fibers on each turn.
   - **If ANY `GuidanceInstruction` card triggers on a turn, Knowledge Assist (PGKA) output is suppressed for that turn.**
   - **Migration Fix**: Never write catch-all Guidance cards (e.g., `"Apply on every customer question"`). Keep Guidance card `condition` fields narrow and specific to compliance/procedural scenarios so general informational questions fall through cleanly to Knowledge Assist.

---

## 4. Known Behavioral Differences Summary

| Behavior | Legacy AI Coach / PGKA | Companion Agent |
| :--- | :--- | :--- |
| **`displayDetails` Prompt Visibility** | Visible to model in some generator templates | **Strictly UI-only** (never sent to LLM) |
| **Multi-Turn Step Tracking** | Stateless or coarse `duplicateCheckResult` | Stateful `completed_actions: [1, 2]` per card across turns (stalls if an action has no observable transcript/tool event) |
| **Tool Execution Gate** | Immediate or basic function call | Enforces **6 backend preconditions** + UI `NEEDS_CONFIRMATION` AnswerRecord gate |
| **Guidance vs. Knowledge Assist** | Separate UI widgets / independent streams | Unified `<agent-assist-companion-agent>` feed; active Guidance card cancels Knowledge Assist for that turn |
| **Pre-Handoff Virtual Agent Turns** | Often evaluated indiscriminately | Companion Agent coaches only after handoff to `HUMAN_ASSIST_STAGE` |
