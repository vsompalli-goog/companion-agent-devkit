# Workflow Agent API Reference, Answer Record Updates & UI Concurrency (FR-1.4)

This reference covers the REST `v2beta1` contracts for **Companion Agent Workflows**, **Answer Record updates** (tool confirmation/cancellation & feedback), and **UI concurrency handling** when embedding `<agent-assist-companion-agent>` with `UiModulesConnector` (`common.js`).

> **Why Pure REST `v2beta1`?**
> Public Python SDK protobuf definitions (`google-cloud-dialogflow`) strip preview fields such as `companionSuggestionInput`, `generateCompanionSuggestionsResponse`, `companionAgents`, and `companionAgentWorkflows`. Always use `google.auth.transport.requests.AuthorizedSession` directly against `https://{location}-dialogflow.googleapis.com/v2beta1/...` with `X-Goog-User-Project: <project_id>`.

---

## 1. Workflow Agent Lifecycle (`companionSuggestionInput.workflowState`)

All workflow state transitions are sent via `POST /v2beta1/{human_agent_participant}:analyzeContent` on the `HUMAN_AGENT` participant using `companionSuggestionInput.workflowState`.

### 1.1 Starting a Workflow
```json
{
  "companionSuggestionInput": {
    "workflowState": {
      "workflow": "projects/my-proj/locations/global/companionAgentWorkflows/billing-dispute",
      "workflowDetails": {
        "workflow": "projects/my-proj/locations/global/companionAgentWorkflows/billing-dispute",
        "displayName": "Billing Dispute Resolution"
      },
      "state": "RUNNING",
      "stage": "IN_PROGRESS"
    }
  }
}
```

### 1.2 Marking a Step Complete (`DONE` / `USER_SKIPPED`)
```json
{
  "companionSuggestionInput": {
    "workflowState": {
      "workflow": "projects/my-proj/locations/global/companionAgentWorkflows/billing-dispute",
      "state": "RUNNING",
      "stage": "IN_PROGRESS",
      "steps": [
        {
          "details": { "id": "verify_identity_step" },
          "state": "DONE"
        }
      ],
      "stepStates": [
        {
          "name": "verify_identity_step",
          "stage": "COMPLETED",
          "completeReason": "USER_COMPLETED"
        }
      ]
    }
  }
}
```
*(If the agent clicked "Skip Tool" in the UI, set `"completeReason": "USER_SKIPPED"`.)*

### 1.3 Reverting a Completed Step (`ACTIVE`)
```json
{
  "companionSuggestionInput": {
    "workflowState": {
      "workflow": "projects/my-proj/locations/global/companionAgentWorkflows/billing-dispute",
      "state": "RUNNING",
      "steps": [
        {
          "details": { "id": "verify_identity_step" },
          "state": "ACTIVE"
        }
      ]
    }
  }
}
```

### 1.4 Updating Step Variables (`stepVariables`)
```json
{
  "companionSuggestionInput": {
    "workflowState": {
      "workflow": "projects/my-proj/locations/global/companionAgentWorkflows/billing-dispute",
      "state": "RUNNING",
      "stepVariables": [
        {
          "name": "disputed_amount",
          "value": "49.99"
        }
      ]
    }
  }
}
```

### 1.5 Exiting a Workflow
```json
{
  "companionSuggestionInput": {
    "workflowState": {
      "workflow": "projects/my-proj/locations/global/companionAgentWorkflows/billing-dispute",
      "state": "MANUALLY_EXITED",
      "stage": "EXITED",
      "exitReason": "USER_INITIATED"
    }
  }
}
```

---

## 2. Answer Record Updates (Tool Confirmation & Feedback)

### 2.1 Confirming or Cancelling a Pending Tool Call (`suggestionInput`)
When a tool with `confirmationRequirement: "REQUIRED"` pauses in `NEEDS_CONFIRMATION`, the response includes an `answerRecord` resource path inside `toolCallInfo.toolCall.answerRecord`.

To **Confirm** (`"CONFIRM"`) or **Skip/Cancel** (`"CANCEL"`), call `POST /v2beta1/{human_agent_participant}:analyzeContent`:

```json
{
  "suggestionInput": {
    "answerRecord": "projects/my-proj/locations/global/answerRecords/ar-12345",
    "action": "CONFIRM",
    "parameters": {
      "account_id": "AC-90210",
      "refund_amount": 25.0
    },
    "sendTime": "2026-10-07T16:00:00Z"
  }
}
```

### 2.2 Updating Answer Record Feedback (`PATCH /v2beta1/{answerRecord}`)
When the human agent clicks Thumbs Up/Down, copies a suggestion, or logs UI feedback on a guidance card, summary, or workflow suggestion:

- **Endpoint**: `PATCH /v2beta1/{answer_record_name}?updateMask=answer_feedback` (or `updateMask=agentAssistantRecord` when emitted by `<agent-assist-companion-agent>`'s `patch-answer-record-requested` event).
- **Body**:
```json
{
  "name": "projects/my-proj/locations/global/answerRecords/ar-12345",
  "answerFeedback": {
    "correctnessLevel": "FULLY_CORRECT",
    "clicked": true,
    "displayed": true
  }
}
```

---

## 3. UI Side Concurrency & Race Handling (`<agent-assist-companion-agent>`)

When integrating the official `<agent-assist-companion-agent>` web component alongside `UiModulesConnector` (`common.js`), three subtle client-side concurrency hazards occur unless explicitly handled by your UI bridge:

### Hazard 1: Duplicate `:analyzeContent` Calls on Tool Confirmation
- **Root Cause**: When a human agent clicks **Confirm** on a tool card, `<agent-assist-companion-agent>` dispatches `tool-action-requested` AND `common.js` (`UiModulesConnector`) also intercepts `analyze-content-requested`. Without deduplication, two parallel `POST ...:analyzeContent` requests fire with the exact same `suggestionInput.answerRecord` within milliseconds — causing backend race conditions or duplicate state mutations.
- **Solution (2.5s `answerRecord` Promise Cache)**:
  - Key a short-lived `Map<answerRecord, { timestamp, promise }>` in your fetch interceptor or proxy handler.
  - If a second `:analyzeContent` call arrives for the same `answerRecord` within `2500ms`, await and return the **same in-flight Promise** instead of firing a second HTTP request.

### Hazard 2: `UiModulesConnector` Event Drop / Race (`150ms–180ms` Fallback Window)
- **Root Cause**: `<agent-assist-companion-agent>` tracks its tool loading spinner (`store.mg`) via `analyze-content-requested`, while `UiModulesConnector` listens for outbound events to fire `fetch('/v2beta1/...:analyzeContent')`. If the connector has not finished initializing participants or drops `tool-action-requested`, the UI button spins forever.
- **Solution**:
  1. When `tool-action-requested` fires, immediately synthesize and dispatch `analyze-content-requested` (marked with `_fromBridge: true` to prevent infinite loops) so the widget sets its loading state properly.
  2. Record a `_pendingToolAction = { nonce, answerRecord, fetchedByConnector: false }` token.
  3. Set a `150ms` timer (`180ms` for workflow `analyze-content-requested`). When the fetch interceptor sees `UiModulesConnector` issue the `:analyzeContent` HTTP call, flip `fetchedByConnector = true`. If the timer expires and `fetchedByConnector === false`, execute the `:analyzeContent` request directly from the bridge and dispatch `analyze-content-response-received`.

### Hazard 3: Store Ingestion Parity (`toolCallHistory` vs. `guidances[].toolCalls`)
- **Root Cause**: Different versions of `<agent-assist-companion-agent>` read tool call states from two places in `generateCompanionSuggestionsResponse`:
  - `workflowState.toolCallHistory[].toolCall.toolCallInfo`
  - `companionSuggestion.guidances[].toolCalls[].toolCallInfo`
- **Solution**: Whenever a tool confirmation response returns, mirror the updated `toolCallInfo` (matching by `answerRecord`) into **both** `workflowState.toolCallHistory` and `guidances[0].toolCalls` before dispatching `analyze-content-response-received`.

See [examples/workflow_api_samples.py](../examples/workflow_api_samples.py) and [examples/ui_concurrency_bridge_sample.js](../examples/ui_concurrency_bridge_sample.js) for copy-ready reference implementations.
