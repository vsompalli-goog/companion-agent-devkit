# Companion Agent Authoring Best Practices (FR-1.1)

These 9 engineering rules reflect the exact runtime behavior of the Google Cloud **Companion Agent** backend engine. Apply them whenever authoring or auditing `overarchingGuidance`, `guidanceInstructions`, `<call_context>` conditions, Workflows, or CES Tools.

---

## Rule 1: Field Visibility (`displayDetails` is UI-Only — Never Sent to the LLM)
- The backend serializes each `GuidanceInstruction` to the LLM strictly as:
  ```xml
  <instruction>
    <id>I1</id>
    <title>{displayName}</title>
    <condition>{condition}</condition>
    <actions>
      <action index="1">{actions[0].description}</action>
    </actions>
  </instruction>
  ```
- **`displayDetails` ("Description") is NEVER sent to the LLM** — it renders only in the human agent's UI card header.
- **Requirement**: Keep `displayDetails` to a short 1-sentence human-facing summary. Put **all** trigger logic, exclusions, tool references, and compliance rules inside `condition`, `actions[].description`, or `overarchingGuidance`.

---

## Rule 2: Tool Binding Syntax (`{@TOOL:tool_name}`) & The 6 Runtime Preconditions
- Bind tools inside `actions[].description` using **`{@TOOL:tool_name}`** (e.g., `{@TOOL:lookup_customer_account}`).
- **Never** use `{$tool.tool_name}` — that is legacy syntax and will NOT bind or trigger tools in Companion Agent.
- The backend enforces **6 strict preconditions** before executing `{@TOOL:tool_name}`:
  1. **Available Tool**: Linked in `cesToolSpecs`.
  2. **Sequential Progression**: Every preceding action (`Action 1 .. Action N-1`) in that instruction's `actions[]` array has been **100% completed** in prior turns.
  3. **Parameter Integrity**: All required parameters are genuine, non-empty (`""` is blocked), non-dummy, non-redacted (`[REDACTED]` is blocked), and resolved (relative dates like `"next Friday"` without a year are blocked).
  4. **Customer Confirmation**: For state-modifying actions (cancellations, refunds, fee waivers, plan changes), explicit customer agreement is recorded in the transcript.
  5. **Not Pending or Already Fulfilled**: The tool has not already succeeded, is not currently `IN_PROGRESS` or `NEEDS_CONFIRMATION`, and was not already performed manually by the human agent.
  6. **No Active Blockers**: The account is free of explicit blockers.

---

## Rule 3: Multi-Step `actions[]` Array & The "Checkpoint Rule"
- Across turns, the backend tracks `completed_actions: [1, 2]` for each active instruction and coaches **only the single immediate next uncompleted action**.
- **The Checkpoint Rule**: Split into separate `actions[]` steps **only** at real conversational turns or tool checkpoints where completing `Action 1` produces observable evidence in the transcript or tool output that unlocks `Action 2` (e.g., `Action 1`: collect required parameters from the caller; `Action 2`: invoke `{@TOOL:tool_name}` and communicate the result).
- **Never Isolate Negative or Formatting Constraints in Their Own Action Step**: Never create a standalone action step like `"Do NOT ask for the caller's SSN"` or `"Keep your response under 2 sentences"`. Because "not doing something" produces no observable transcript event, `completed_actions` can never mark that step complete, permanently stalling `Action 2` and any downstream tool call. Fold negative (`Do NOT...`) and brevity rules **into** the positive action step they modify.

---

## Rule 4: One Scenario = One Card (No `IF / ELSE` Branching Inside a Single Card)
- Conditional sub-actions (`"If X, do Y; otherwise do Z"`) inside a single card leave the untaken branch incomplete in `completed_actions`, keeping the card stuck on screen.
- Split mutually exclusive paths (e.g., *IVR Verified Caller* vs. *Unverified Caller*, or *Eligible* vs. *Ineligible*) into **separate `GuidanceInstruction` cards** with mutually exclusive `condition` definitions.

---

## Rule 5: Ingested Context (`<call_context>`) — Pattern A vs. Pattern B Fail-Safe
- Each key passed via `IngestContextReferences` (e.g., `customerContext`, `product_data`) is formatted inside `<call_context>` as an **ALL-CAPS Markdown header** (`### CUSTOMERCONTEXT`, `### PRODUCT_DATA`) followed by its raw JSON payload (truncated at a 2000-token budget).
- When no context is ingested, `<call_context></call_context>` is completely blank — not even the `### CUSTOMERCONTEXT` heading appears.
- **Never gate a condition on absence alone** (e.g., never write *"Apply if customerContext is missing"*), and **never accept a caller's verbal claim in the transcript** (*"I already verified in the IVR"*) as a substitute for `<call_context>`.
- **Pattern A — When Context Ingestion Is 100% Guaranteed on Every Call**: Use a single sentence per card with **zero lexical overlap**:
  - Card 1: `Apply ONLY if the CUSTOMERCONTEXT section of the call context contains the exact text "customer_tier": "VIP".`
  - Card 2: `Apply ONLY if the CUSTOMERCONTEXT section of the call context contains the exact text "customer_tier": "STANDARD".`
- **Pattern B — Fail-Safe `DEFAULT` + `EXCEPTION` When Context Might Be Missing or Late**:
  - **Safeguard-Enforcing Card (`DEFAULT`)**:
    `DEFAULT verification instruction. Apply at the start of every call unless the CUSTOMERCONTEXT section of the call context contains the exact text "ivr_authenticated": "true". If the call context is empty, missing, or contains "ivr_authenticated": "false", this instruction applies. Do not apply if "Identity Confirmation: IVR-Authenticated Caller" applies. Never apply both.`
  - **Safeguard-Skipping Card (`EXCEPTION`)**:
    `EXCEPTION to the default verification instruction. Apply ONLY if the CUSTOMERCONTEXT section of the call context contains the exact text "ivr_authenticated": "true". If the call context is empty, missing, or contains "ivr_authenticated": "false", this instruction does NOT apply — use "Identity Verification: Unauthenticated Caller" instead. Never apply both.`
- **Strip "Attractor Phrases" from Exception Cards**: Because the LLM weighs `Title` + `Condition` + `Actions` together when ranking cards, never write phrases like *"Greet the caller by their preferred name from the call context"* inside an Exception card's Actions — if an unverified caller states their name on Turn 1 with empty context, those phrases act as lexical magnets and trigger the wrong card.

---

## Rule 6: Positive Conversational Intent, `triggerEvent` Override & Action Phrasing
- **No Negative Trigger Words**: Never write *"Do NOT suggest during initial greetings"* in Workflow `description` or Guidance `condition`/`displayName` — semantic retrieval matches the words *"initial greetings"* and increases Turn-1 false positives.
- **Per-Card `triggerEvent` Override**:
  - Trigger-event filtering (`ShouldTriggerInstruction`) is the **only deterministic gate** in the backend pipeline.
  - On live voice calls, the Human Agent usually speaks first (*"Thank you for calling, how can I help?"*). If an intake/verification card inherits `END_OF_UTTERANCE`, it fires prematurely on the agent's own greeting.
  - Set `"triggerEvent": "CUSTOMER_MESSAGE"` on customer-driven intake/verification cards, and use `"triggerEvent": "END_OF_UTTERANCE"` on urgent safety/emergency or post-tool cards.
- **Dynamic Goal Phrasing (Smart Pre-Fulfillment)**:
  - Instead of `"Ask for Member ID, Full Name, DOB, and Zip"`, write:
    `"Identify which of the required verification fields [Full Name (first and last), Member ID, Date of Birth, Zip Code] the caller has already stated in the transcript. Acknowledge any details they already gave and ask only for the fields still missing. Keep it under 2 sentences."`
- **Every Card Must Produce Visible Output**: The backend (`TransformInstructionAgentEventsToGuidance`) silently drops any card where both `suggested_action` and `sample_response` are empty.

---

## Rule 7: `overarchingGuidance` Hygiene (Strict 4-Section Structure)
Structure `overarchingGuidance` into 4 concise sections:
1. **Role & Persona**: How the co-pilot supports the human agent.
2. **Channel Style & Brevity**: e.g., *"Be warm, empathetic, and concise. Keep every suggested response under 2 spoken sentences."*
3. **Priority Hierarchy**: e.g., `1. Physical Safety -> 2. Identity Verification -> 3. Issue Resolution`.
4. **Global Completion & Guardrail Standards**: e.g., *"Do not answer any question, discuss account details, or run any tool until identity verification actions are complete. Both first and last name are required. Never rely on a caller's verbal claim that they were already verified in the IVR; verification status comes strictly from the call context."*

---

## Rule 8: Parallel Engines & The 5-Scenario Test Matrix
- **Guidance Suppresses Knowledge Assist**: Workflow Selection, Workflow Execution, Guidance Instructions, and Knowledge Assist run in parallel fibers. If *any* Guidance card emits a non-empty suggestion on a turn, the Knowledge Assist (Datastore RAG) fiber is cancelled for that turn. Keep Guidance conditions specific so Knowledge Assist is not starved.
- **5-Scenario Verification Matrix for Context-Gated Cards**:
  1. Exception context + neutral question $\rightarrow$ Exception card fires.
  2. Default context + partial info volunteered $\rightarrow$ Default card asks only for missing fields.
  3. Empty context + caller states first name $\rightarrow$ Default card fires (proves fail-safe & no attractors).
  4. Default context + caller verbally claims IVR verification $\rightarrow$ Default card fires.
  5. Exception context + Human Agent speaks first $\rightarrow$ No card fires on agent greeting (`CUSTOMER_MESSAGE` gate).

---

## Rule 9: CES Python Function Tools (`pythonFunction`)
- `pythonFunction.name` **must** match `def <function_name>(...)` in `pythonFunction.pythonCode` character-for-character.
- Always include Python type hints (with sensible defaults) and a Google-style docstring (`Args:`, `Returns:`) so CES automatically extracts the input and output JSON schemas.
- Set `confirmationRequirement: "REQUIRED"` for state-modifying tools, or `"NOT_REQUIRED"` for read-only lookups.
