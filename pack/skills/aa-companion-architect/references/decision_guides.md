# Companion Agent Decision Guides (FR-1.2)

This guide helps FDEs and customer engineers make architectural choices when designing a Google Cloud Agent Assist **Companion Agent**:
1. **Reactive Query vs. Proactive Suggestion**
2. **Tool Type Selection (MCP Tool vs. Python Tool vs. OpenAPI Tool vs. Datastore Tool)**
3. **Instruction Agent (`GuidanceSkillConfig`) vs. Workflow Agent (`CompanionAgentWorkflow`)**

---

## 1. Reactive Query vs. Proactive Suggestion

Companion Agent supports two complementary invocation paradigms on an active conversation:

| Dimension | Proactive Suggestion (`AnalyzeContent`) | Reactive Query ("Ask Assistant") |
| :--- | :--- | :--- |
| **Trigger** | Automatic on conversation turns (`END_OF_UTTERANCE`, `CUSTOMER_MESSAGE`, `AGENT_MESSAGE`) | Explicit human agent query via `textInput.companionQuery` or `:streamCompanionReactiveQuestion` |
| **Primary Goal** | Real-time compliance, identity verification, guided troubleshooting, automatic workflow/tool coaching | Ad-hoc policy lookup, long-tail questions, on-demand account lookup without cluttering the proactive feed |
| **UI Surface** | Main Guidance feed & Workflow cards in `<agent-assist-companion-agent>` | "Ask Assistant" chat bar / streaming response panel |
| **Latency Expectation** | Evaluated per turn; high-signal precision required so agents aren't overwhelmed | Streaming (`streamCompanionReactiveQuestion`) or synchronous on-demand response |
| **Configuration Flags** | Set `"proactiveEnabled": true` on `cesToolSpecs[]` / `companionAgentWorkflowSpecs[]` | Set `"reactiveEnabled": true` on `cesToolSpecs[]` / `companionAgentWorkflowSpecs[]` |

### Decision Rules: Proactive vs. Reactive
- **Use Proactive** when:
  - Every agent **must** follow a policy or sequence (e.g., mandatory caller verification, safety escalation, regulatory disclosure, fee-waiver eligibility check).
  - The trigger is directly observable in the customer's utterance or `<call_context>` (e.g., `"customer asks to dispute a charge"`).
- **Use Reactive** when:
  - The human agent needs to look up edge-case policies, compare plan features on demand, or run a lookup tool without waiting for the customer to say a specific phrase.
  - Adding another proactive card would create noise or starve **Knowledge Assist** (remember: *any triggered proactive Guidance card cancels Knowledge Assist for that turn*).
- **Enable Both (`proactiveEnabled: true, reactiveEnabled: true`)** on core read-only lookup tools (e.g., `lookup_customer_account`, `check_order_status`) so the agent gets automatic lookups during structured flows AND can query them manually in the "Ask Assistant" bar.

---

## 2. Tool Type Selection Matrix

Choose the right tool integration mechanism based on data structure, determinism, hosting, and authentication requirements:

| Tool Type | Best For | Schema & Auth Mechanism | Key Trade-offs & Guardrails |
| :--- | :--- | :--- | :--- |
| **CES Python Tool** (`pythonFunction`) | Deterministic business logic, math/eligibility calculations, composite API orchestration, response reshaping/filtering before passing to the LLM | Python type hints + Google-style docstring (`Args:`, `Returns:`) auto-generate the JSON schema on CES (`v1beta`). Runs in managed CES Python sandbox. | `pythonFunction.name` **must** match `def <name>(...)` character-for-character. Ideal when raw backend APIs return bloated payloads that need trimming. |
| **OpenAPI Tool** (`openApiTool` / `openApiSpec`) | Existing first-party REST microservices with clean OpenAPI 3.0 specifications and standard OAuth/Bearer/Service-Account auth | OpenAPI 3.0 YAML/JSON (`textSchema` / `openApiSchema`) with explicit `operationId`, parameter schemas, and server URLs. | Zero code to maintain, but raw API responses go directly to the model context. Ensure response payloads are concise. |
| **MCP Tool** (Model Context Protocol) | Connecting to standardized enterprise MCP servers, multi-tool hubs, or third-party ecosystems that already expose an MCP endpoint | Configured via CES MCP / Connector tool integration; dynamically exposes tool schemas registered on the MCP server. | Great for standardized tool catalogs across multiple AI agents; monitor network hop latency and scope only necessary tools to `cesToolSpecs`. |
| **Datastore Tool** (Vertex AI Search / Agentic RAG Knowledge Assist) | Unstructured or semi-structured knowledge bases: PDFs, HTML help centers, SOP manuals, troubleshooting articles, FAQs | Configured on `ConversationProfile` (`featureConfigs` with `KNOWLEDGE_ASSIST` / `AGENTIC_KNOWLEDGE_ASSIST` + `queryConfig` & datastore rewriter). | **Critical Engine Interaction**: On any turn where a `GuidanceInstruction` card triggers, Knowledge Assist is suppressed for that turn. Keep Guidance conditions narrow so Datastore RAG can answer general questions. |

### Quick Tool Selection Flowchart

```text
Is the data unstructured prose (manuals, FAQs, policy PDFs)?
  ├── YES -> Use Datastore Tool (Knowledge Assist / Vertex AI Search on ConversationProfile)
  └── NO (Structured API / Action / Calculation)
        ├── Is it hosted on an existing MCP Server?
        │     └── YES -> Use MCP Tool
        ├── Do you need data transformation, multi-call orchestration, or custom Python math?
        │     └── YES -> Use CES Python Tool (`pythonFunction`)
        └── Is it a clean, single-hop REST endpoint with an OpenAPI 3.0 spec?
              └── YES -> Use OpenAPI Tool (`openApiTool` / `openApiSpec`)
```

### State-Modifying vs. Read-Only Tools (`confirmationRequirement`)
- **Read-Only Lookups** (`get_*`, `lookup_*`, `list_*`, `check_*`): Set `"confirmationRequirement": "NOT_REQUIRED"` so the Companion Agent executes them automatically once preconditions are met.
- **State-Modifying Mutations** (`cancel_*`, `refund_*`, `update_*`, `create_*`, `waive_*`): Always set `"confirmationRequirement": "REQUIRED"` so `<agent-assist-companion-agent>` pauses in `NEEDS_CONFIRMATION` and renders an explicit **Confirm / Skip** UI card for the human agent.

---

## 3. Instruction Agent (`GuidanceSkillConfig`) vs. Workflow Agent (`CompanionAgentWorkflow`)

| Dimension | Instruction Agent (`GuidanceSkillConfig`) | Workflow Agent (`CompanionAgentWorkflow`) |
| :--- | :--- | :--- |
| **Structure** | Flat list of independent `GuidanceInstruction` cards, each with `condition` + sequential `actions[]` | Directed graph (DAG) of typed steps (`STEP_TYPE_COLLECT`, `STEP_TYPE_TOOL_CALL`, `STEP_TYPE_INFORM`) with `outputVariableMappings` and branch transitions |
| **Branching (`IF / ELSE`)** | **Forbidden inside a single card** (*One Scenario = One Card*). Requires splitting into mutually exclusive cards. | **First-class support** via step transitions and deterministic `outputVariableMappings` conditions. |
| **State Tracking** | Tracks `completed_actions: [1, 2]` per card across turns | Explicit `workflowState` (`steps[]`, `stepVariables[]`, `toolCallHistory[]`) with UI step progress bar, manual step completion, and step revert |
| **When to Choose** | Compliance guardrails, identity verification, 1–3 step linear procedures, context-gated alerts, safety escalations | Multi-step, branching SOPs (e.g., complex billing dispute, hardware RMA troubleshooting, plan migration) where agents need visual step tracking and variable extraction |
