---
name: aa-workflow-generator
description: Generate, normalize, and debug Google Cloud Agent Assist CompanionAgentWorkflow JSON DAGs from SOPs, flowcharts, or prompts. Use whenever creating or updating multi-step Companion Agent workflows (INFORMATION_COLLECTION, SYSTEM_ACTION, INFORMATIONAL_MESSAGE, MANUAL_ACTION, END_WORKFLOW), wiring CES tool outputVariableMappings and conditional branch transitions, or troubleshooting WorkflowSelectionAgent and WorkflowExecutionAgent behavior.
---

# Companion Agent Workflow Generator & DAG Architect (`aa-workflow-generator`)

Use this skill whenever translating a contact center Standard Operating Procedure (SOP), troubleshooting decision tree, or user prompt into a valid **Google Cloud Dialogflow `v2beta1` `CompanionAgentWorkflow`** JSON configuration, or when debugging workflow selection and step progression issues.

## Progressive Disclosure Reference Map

1. **Strict `CompanionAgentWorkflow` Protobuf, DAG & C++ Runtime Rules**:
   - Read [references/workflow_authoring_and_dag_rules.md](references/workflow_authoring_and_dag_rules.md) for:
     - Exact `:publish` validator rules (`displayName <= 63` chars, positive `description`, `rootStepId`, cycle-free DAG with zero orphan steps terminating at `END_WORKFLOW`).
     - The 5 `CompanionAgentWorkflowStep.Type` schemas (`INFORMATION_COLLECTION`, `SYSTEM_ACTION`, `INFORMATIONAL_MESSAGE`, `MANUAL_ACTION`, `END_WORKFLOW`).
     - Globally unique `Variable` declarations (`stringType`, `numberType`, `booleanType`, `enumType`), `outputVariableMappings`, and conditional `transitions` (`variableCondition`).
     - Production C++ runtime separation between **`WorkflowSelectionAgent`** and **`WorkflowExecutionAgent`**.
2. **Good vs. Bad Workflow JSON Examples**:
   - Read [examples/workflow_good_vs_bad.json](examples/workflow_good_vs_bad.json) for side-by-side anti-patterns vs. a `:publish`-ready branching `CompanionAgentWorkflow` + linked `companionAgentWorkflowSpecs` and CES Python tools.
3. **Runtime `:analyzeContent` Workflow State Transitions**:
   - If you need the runtime API payloads (`companionSuggestionInput.workflowState` to start, complete, skip, revert, or exit a workflow), read [../aa-companion-architect/references/workflow_api_and_ui_concurrency.md](../aa-companion-architect/references/workflow_api_and_ui_concurrency.md).

## CLI Workflow Generation & Normalization (`aa-devkit generate-workflow`)

You can invoke the stateless CLI to compile a **PDF SOP document (`.pdf`)**, a **Markdown/Text SOP (`.md`/`.txt`)**, or a **shorthand JSON spec (`.json`)** into a DAG-verified `CompanionAgentWorkflow` JSON (and optionally inject it into an exported bundle):

```bash
# 1. Generate a DAG-verified CompanionAgentWorkflow directly from a PDF SOP document (.pdf)
aa-devkit generate-workflow evals/samples/sample_billing_dispute_sop.pdf \
  --bundle evals/samples/sample_agent_bundle \
  --output ./generated_pdf_workflow.json

# 2. (Optional) Use Vertex AI Gemini multimodal (application/pdf inlineData) + a custom --prompt
aa-devkit generate-workflow evals/samples/sample_billing_dispute_sop.pdf \
  --bundle evals/samples/sample_agent_bundle \
  --use-vertex --project my-gcp-project \
  --prompt "Focus on the refund steps and require agent confirmation before calling issue_refund." \
  --output ./generated_pdf_workflow.json

# 3. Or generate a workflow purely from a natural-language --prompt (no file required)
aa-devkit generate-workflow \
  --use-vertex --project my-gcp-project \
  --bundle evals/samples/sample_agent_bundle \
  --prompt "Create a 4-step Billing Dispute workflow: 1. Ask for order_id and disputed_amount. 2. Call lookup_order. 3. Call issue_refund with confirmationRequirement=REQUIRED. 4. Inform caller of refund confirmation." \
  --output ./generated_prompt_workflow.json

# 4. Compile a shorthand JSON workflow spec into a DAG-verified v2beta1 CompanionAgentWorkflow JSON
aa-devkit generate-workflow evals/samples/sample_workflow_sop_spec.json \
  --bundle evals/samples/sample_agent_bundle \
  --output ./generated_workflow.json
```

## Mandatory Workflow Generation Checklist

Before outputting any `CompanionAgentWorkflow` JSON, verify:
- [ ] `displayName` is **$\le 63$ characters** (enforced by `companion_agent_workflow.proto`).
- [ ] `description` states **positive** customer-intent entry criteria (no `"Do NOT suggest during initial greetings"`). Remember: `WorkflowSelectionAgent` reads **only** `displayName` and `description`.
- [ ] `rootStepId` matches the `id` of the entry step in `steps[]`.
- [ ] **100% of steps** in `steps[]` are reachable from `rootStepId` with **zero cycles** (strict DAG), and every branch path terminates at an `"END_WORKFLOW"` step.
- [ ] Every `"END_WORKFLOW"` step has **no** `actions` field and **no** `transitions` field.
- [ ] Every `"INFORMATION_COLLECTION"` step has at least 1 variable in `variables[]`, and every variable `name` (`snake_case`) is **globally unique across the entire workflow**.
- [ ] Every `"SYSTEM_ACTION"` step binds a valid `cesToolSpec` (`confirmationRequirement: "REQUIRED"` for mutations, `"NOT_REQUIRED"` for lookups) and maps tool outputs used in downstream branches via `outputVariableMappings`.
- [ ] The workflow is linked in `companion_agent.json` under `companionAgentWorkflowSpecs[]` with `"proactiveEnabled": true`.
