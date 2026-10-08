"""Reference Python samples for Companion Agent Workflow APIs & Answer Record updates (FR-1.4).

Demonstrates pure REST `v2beta1` calls using `google.auth.transport.requests.AuthorizedSession`
so preview fields (`companionSuggestionInput`, `suggestionInput`, `answerFeedback`) are
preserved without being stripped by public Python SDK protobuf definitions.
"""

from __future__ import annotations

import datetime
from typing import Any, Optional

import google.auth
from google.auth.transport.requests import AuthorizedSession


class CompanionWorkflowRuntimeSample:
    """Minimal stateless REST v2beta1 helper for Workflow & AnswerRecord operations."""

    def __init__(self, project_id: str, location: str = "global"):
        self.project_id = project_id
        self.location = location
        self.base_url = (
            "https://dialogflow.googleapis.com"
            if location == "global"
            else f"https://{location}-dialogflow.googleapis.com"
        )
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.session = AuthorizedSession(creds)

    def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        headers = {
            "Content-Type": "application/json",
            "X-Goog-User-Project": self.project_id,
        }
        resp = self.session.request(method, url, headers=headers, json=json_body, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json() if resp.text else {}

    def start_workflow(self, human_agent_participant: str, workflow_resource: str, display_name: str = "") -> dict[str, Any]:
        """Starts a CompanionAgentWorkflow on the HUMAN_AGENT participant."""
        body = {
            "companionSuggestionInput": {
                "workflowState": {
                    "workflow": workflow_resource,
                    "workflowDetails": {
                        "workflow": workflow_resource,
                        "displayName": display_name or workflow_resource.split("/")[-1],
                    },
                    "state": "RUNNING",
                    "stage": "IN_PROGRESS",
                }
            }
        }
        return self._request("POST", f"v2beta1/{human_agent_participant}:analyzeContent", json_body=body)

    def complete_or_skip_step(
        self,
        human_agent_participant: str,
        workflow_resource: str,
        step_id: str,
        skipped: bool = False,
    ) -> dict[str, Any]:
        """Marks a workflow step DONE (USER_COMPLETED or USER_SKIPPED)."""
        body = {
            "companionSuggestionInput": {
                "workflowState": {
                    "workflow": workflow_resource,
                    "state": "RUNNING",
                    "stage": "IN_PROGRESS",
                    "steps": [{"details": {"id": step_id}, "state": "DONE"}],
                    "stepStates": [
                        {
                            "name": step_id,
                            "stage": "COMPLETED",
                            "completeReason": "USER_SKIPPED" if skipped else "USER_COMPLETED",
                        }
                    ],
                }
            }
        }
        return self._request("POST", f"v2beta1/{human_agent_participant}:analyzeContent", json_body=body)

    def revert_step(self, human_agent_participant: str, workflow_resource: str, step_id: str) -> dict[str, Any]:
        """Reverts a completed workflow step back to ACTIVE."""
        body = {
            "companionSuggestionInput": {
                "workflowState": {
                    "workflow": workflow_resource,
                    "state": "RUNNING",
                    "steps": [{"details": {"id": step_id}, "state": "ACTIVE"}],
                }
            }
        }
        return self._request("POST", f"v2beta1/{human_agent_participant}:analyzeContent", json_body=body)

    def exit_workflow(self, human_agent_participant: str, workflow_resource: str) -> dict[str, Any]:
        """Exits an active workflow (USER_INITIATED)."""
        body = {
            "companionSuggestionInput": {
                "workflowState": {
                    "workflow": workflow_resource,
                    "state": "MANUALLY_EXITED",
                    "stage": "EXITED",
                    "exitReason": "USER_INITIATED",
                }
            }
        }
        return self._request("POST", f"v2beta1/{human_agent_participant}:analyzeContent", json_body=body)

    def confirm_or_cancel_tool_call(
        self,
        human_agent_participant: str,
        answer_record: str,
        confirmed: bool = True,
        parameters: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        """Confirms ('CONFIRM') or skips ('CANCEL') a pending NEEDS_CONFIRMATION tool call."""
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        body = {
            "suggestionInput": {
                "answerRecord": answer_record,
                "action": "CONFIRM" if confirmed else "CANCEL",
                "parameters": parameters or {},
                "sendTime": now_iso,
            }
        }
        return self._request("POST", f"v2beta1/{human_agent_participant}:analyzeContent", json_body=body)

    def patch_answer_record_feedback(
        self,
        answer_record_name: str,
        clicked: bool = True,
        correctness_level: str = "FULLY_CORRECT",
        update_mask: str = "answer_feedback",
    ) -> dict[str, Any]:
        """Updates an AnswerRecord with human agent feedback (clicks, thumbs up/down)."""
        body = {
            "name": answer_record_name,
            "answerFeedback": {
                "clicked": clicked,
                "displayed": True,
                "correctnessLevel": correctness_level,
            },
        }
        return self._request(
            "PATCH",
            f"v2beta1/{answer_record_name}",
            json_body=body,
            params={"updateMask": update_mask},
        )
