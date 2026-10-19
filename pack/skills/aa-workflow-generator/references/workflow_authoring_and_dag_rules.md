# Companion Agent Workflow Authoring, DAG Validator & C++ Runtime Rules

This reference documents the exact **Dialogflow `v2beta1` `CompanionAgentWorkflow` protobuf contract** enforced on `CreateCompanionAgentWorkflow`, `UpdateCompanionAgentWorkflow`, and `PublishCompanionAgentWorkflow` (`:publish`), as well as the production C++ runtime behavior of `WorkflowSelectionAgent` and `WorkflowExecutionAgent`.

---

## 1. Strict `CompanionAgentWorkflow` Protobuf & `:publish` Validator Rules

When `:publish` (`POST /v2beta1/{workflow}:publish` with `{"markLive": true}`) is called, the backend validator enforces these hard structural constraints:

1. **`displayName` ($\le 63$ Characters)**:
   - `companion_agent_workflow.proto` enforces a strict **63-character maximum** on `displayName`.
   - Must be concise and unique across all workflows attached to the `CompanionAgent`.
2. **`description` (Positive Customer-Intent Entry Criteria)**:
   - Write positive customer-intent trigger criteria with **zero negative scoping** (never write *"Do NOT trigger during initial greetings"* — semantic matching triggers on the words *"initial greetings"*).
   - Example: `"Trigger when the customer disputes a billed charge, reports an unrecognized fee, or requests a billing credit."`
3. **Fully Connected Acyclic Step Graph (`rootStepId` $\rightarrow$ `END_WORKFLOW`)**:
   - `rootStepId` **must** match the `id` of the starting step in `steps[]`.
   - **100% of steps in `steps[]` must be reachable from `rootStepId`** via `transitions[]` (zero orphaned/unreachable steps).
   - The step graph must contain **no cycles** (strictly a Directed Acyclic Graph / DAG). Any back-edge loop causes `:publish` to reject the workflow.
   - Every branch path **must** terminate at a step with `"type": "END_WORKFLOW"`.

---

## 2. The 5 `CompanionAgentWorkflowStep.Type` Enum Values & Payloads

Every step in `steps[]` must use one of these 5 exact `type` strings:

### 2.1 `INFORMATION_COLLECTION` (Ask User / Collect Slot Variables)
Prompts the human agent to collect 1 or more variables from the customer before advancing.
- **Proto Rules**:
  - `variables[]` **must** contain at least 1 variable.
  - Every variable `name` (`snake_case`, starting with a letter) **must be globally unique across all steps in the entire workflow** (including `outputVariableMappings` in `SYSTEM_ACTION` steps).
  - Variable type oneof must be one of:
    - `"stringType": {}`
    - `"numberType": {}`
    - `"booleanType": {}`
    - `"enumType": {"values": ["OPTION_A", "OPTION_B"]}`

```json
{
  "id": "step_collect_dispute_details",
  "title": "Collect Disputed Charge Details",
  "type": "INFORMATION_COLLECTION",
  "actions": [
    {
      "humanAgentAction": {
        "informationCollectionAction": {
          "displayDetails": {
            "content": "Ask the caller for their Account ID and the disputed charge amount.",
            "enableMarkdown": true
          },
          "messageSuggestionConfig": {
            "content": "I can help look into that charge right away. Could you share your Account ID and the exact dollar amount of the charge?",
            "verbatim": false
          },
          "variables": [
            {
              "name": "customer_account_id",
              "description": "The customer's unique account identifier (e.g. AC-90210). Confirm phonetic letters carefully.",
              "stringType": {}
            },
            {
              "name": "disputed_charge_amount",
              "description": "The dollar amount of the disputed charge.",
              "numberType": {}
            }
          ]
        }
      }
    }
  ],
  "transitions": [
    {
      "targetStepId": "step_check_refund_eligibility"
    }
  ]
}
```

### 2.2 `SYSTEM_ACTION` (Execute CES / Dialogflow Tool & Map Outputs)
Executes a linked tool (`cesToolSpec`) and maps fields from the tool's JSON response into workflow variables via `outputVariableMappings`.
- **Proto Rules**:
  - Set `"confirmationRequirement": "NOT_REQUIRED"` for read-only lookups and `"REQUIRED"` for state-modifying tools (refunds, cancellations, credits).
  - Any variable defined inside `outputVariableMappings[].variable` must also have a globally unique `name` across the workflow.
  - Whenever a `SYSTEM_ACTION` references a tool, ensure that tool is also linked in `companion_agent.json` (`cesToolSpecs[]`) and defined in `tools/`.

```json
{
  "id": "step_check_refund_eligibility",
  "title": "Check Charge Refund Eligibility",
  "type": "SYSTEM_ACTION",
  "actions": [
    {
      "systemAction": {
        "displayDetails": {
          "content": "Run eligibility check for the disputed charge.",
          "enableMarkdown": true
        },
        "cesToolSpec": {
          "cesTool": "projects/my-proj/locations/global/apps/my-app/tools/check_dispute_eligibility",
          "confirmationRequirement": "NOT_REQUIRED",
          "proactiveEnabled": true,
          "reactiveEnabled": true
        },
        "outputVariableMappings": [
          {
            "fieldPaths": [
              {
                "path": "eligibility_status"
              }
            ],
            "variable": {
              "name": "dispute_eligibility_status",
              "description": "Eligibility result returned by check_dispute_eligibility (ELIGIBLE or INELIGIBLE).",
              "enumType": {
                "values": ["ELIGIBLE", "INELIGIBLE"]
              }
            }
          }
        ]
      }
    }
  ],
  "transitions": [
    {
      "targetStepId": "step_issue_credit",
      "condition": {
        "variableCondition": "dispute_eligibility_status == 'ELIGIBLE'"
      }
    },
    {
      "targetStepId": "step_explain_ineligible"
    }
  ]
}
```

### 2.3 `INFORMATIONAL_MESSAGE` (Tell User / Agent Coaching Response)
Coaches the human agent to communicate a specific resolution, disclosure, or explanation to the customer.

```json
{
  "id": "step_explain_ineligible",
  "title": "Explain Dispute Ineligibility & Escalation Option",
  "type": "INFORMATIONAL_MESSAGE",
  "actions": [
    {
      "humanAgentAction": {
        "informationalResponseAction": {
          "displayDetails": {
            "content": "Explain why the charge is outside the automatic credit window and offer a supervisor review.",
            "enableMarkdown": true
          },
          "messageSuggestionConfig": {
            "content": "I see that this charge is outside our 60-day automatic adjustment window, but I can submit a manual billing review for you right away.",
            "verbatim": false
          }
        }
      }
    }
  ],
  "transitions": [
    {
      "targetStepId": "end_workflow"
    }
  ]
}
```

### 2.4 `MANUAL_ACTION` (External Desktop / CRM Action by Human Agent)
Prompts the human agent to perform a manual action in an external CRM or portal where no API tool exists.

```json
{
  "id": "step_verify_id_document",
  "title": "Verify Uploaded ID in Document Portal",
  "type": "MANUAL_ACTION",
  "actions": [
    {
      "humanAgentAction": {
        "manualExternalSystemAction": {
          "displayDetails": {
            "content": "Open the KYC Document Portal tab and confirm the caller's government ID matches the account holder name.",
            "enableMarkdown": true
          }
        }
      }
    }
  ],
  "transitions": [
    {
      "targetStepId": "end_workflow"
    }
  ]
}
```

### 2.5 `END_WORKFLOW` (Terminal Step)
Marks completion of the workflow path.
- **CRITICAL PROTO RULE**: An `END_WORKFLOW` step **must NOT** contain `actions` or `transitions` keys!

```json
{
  "id": "end_workflow",
  "title": "End Workflow",
  "type": "END_WORKFLOW"
}
```

---

## 3. Production C++ Runtime Architecture: `WorkflowSelectionAgent` vs. `WorkflowExecutionAgent`

Understanding how the C++ backend executes workflows is essential when authoring or hill-climbing workflows:

| Engine Component | What It Reads | What It Ignores | How to Fix Common Issues |
| :--- | :--- | :--- | :--- |
| **`WorkflowSelectionAgent`** *(Decides whether to suggest starting a workflow)* | **ONLY** `workflow.displayName` and `workflow.description` | Ignores `companion_agent.overarchingGuidance` and ignores internal `steps[]` before selection | - **Missing or premature workflow suggestion**: Refine the workflow's top-level `description` with explicit positive customer-intent criteria. Putting workflow trigger rules inside `overarchingGuidance` has zero effect on `WorkflowSelectionAgent`. |
| **`WorkflowExecutionAgent`** *(Tracks step completion, extracts variables, and calls tools once active)* | **ONLY** the active workflow JSON (`steps`, `actions`, `variables`, `transitions`) and binds all workflow `cesToolSpecs` globally across all steps | Ignores `companion_agent.overarchingGuidance` and `guidanceInstructions` | - **Step marked DONE before variables collected (`abandoned_step_marked_done_unpopulated`)**: Split multi-variable collection steps or tighten `variables[].description` and `transitions[].condition` so the step only transitions when all required variables are confirmed.<br>- **STT/ASR corrupted variable accepted (`stt_corrupted_variable_acceptance`)**: Add explicit format/phonetic verification instructions inside `variables[].description` and `messageSuggestionConfig.content`.<br>- **Premature tool call during early step**: Because `cesToolSpecs` are bound globally to `WorkflowExecutionAgent`, state explicit preconditions in the `SYSTEM_ACTION` step's `displayDetails` and tool parameter descriptions. |

---

## 4. Linking Workflows to `CompanionAgent` (`companionAgentWorkflowSpecs`)

In `companion_agent.json`, register every workflow under `companionAgentWorkflowSpecs`:

```json
{
  "companionAgentWorkflowSpecs": [
    {
      "workflow": "projects/my-proj/locations/global/companionAgentWorkflows/billing-dispute-workflow",
      "proactiveEnabled": true,
      "reactiveEnabled": true
    }
  ]
}
```
