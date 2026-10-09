"""3-Stage Universal Transcript Importer & Consistent PII Healer (`aa-devkit import-transcript`).

Converts raw contact-center logs from any major CCaaS platform into the standard
`aa-devkit` evaluation dataset schema (`evals/schemas/eval_dataset_schema.json`):
  - Stage 1 (JSON Schema Fingerprinting):
      * Genesys Cloud (`transcripts` -> `phrases` with `participantPurpose`)
      * Amazon Connect Contact Lens (`Transcript` -> `ParticipantId`, `Content`)
      * Google Cloud CCAI / Dialogflow Insights (`entries` or `turns`)
      * Generic JSON lists (`speaker`/`role`/`participant`, `text`/`utterance`/`content`)
  - Stage 2 (CSV/TSV & Timestamped Call-Log Heuristics):
      * CSV/TSV with automatic speaker & utterance column detection
      * Timestamped call logs (`[00:01:12] Agent (Sarah): ...`, `Customer: ...`)
      * Consecutive same-speaker turn merging (`END_USER` + `END_USER` -> 1 turn)
      * Optional `agent_mode="SUGGESTION"` conversion for dynamic agent simulation
  - Stage 3 (Consistent `[REDACTED]` / `[PII]` Placeholder Healer):
      * Detects `[REDACTED]`, `[REDACTED_*]`, `[PII]`, `<REDACTED>`, `***`, `XXXX`
      * Replaces redacted spans with realistic, contextually appropriate synthetic
        entities that remain internally consistent across all turns of the call.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
from typing import Any, Optional

from aa_devkit.client import CompanionAgentRestClient

# Low-signal utterance detector (used to auto-tag `must_suppress: true` on filler/greeting turns)
LOW_SIGNAL_UTTERANCE_RE = re.compile(
    r"^\s*(?:(?:hi|hello|hey|there|good\s+(?:morning|afternoon|evening)|thanks|thank\s+you|"
    r"ok|okay|sure|got\s+it|alright|uh\s*huh|mm\s*hmm|yep|yeah|yes|no\s+problem|"
    r"one\s+moment(?:\s+please)?|hold\s+on(?:\s+please)?|let\s+me\s+check(?:\s+that)?)"
    r"[\s,!?.]*)+$",
    re.IGNORECASE,
)

REDACTION_PATTERN = re.compile(
    r"(\[REDACTED(?:_[A-Z0-9_]+)?\]|\[PII(?:_[A-Z0-9_]+)?\]|<REDACTED(?:_[A-Z0-9_]+)?>|"
    r"\*{3,}|X{4,})",
    re.IGNORECASE,
)

# Deterministic synthetic entity pool (internally consistent across an entire conversation)
DEFAULT_SYNTHETIC_PROFILE = {
    "customer_name": "Alex Rivera",
    "agent_name": "Jordan Taylor",
    "email": "alex.rivera@example.com",
    "phone": "415-555-0192",
    "order_id": "ORD-849201",
    "account_id": "ACCT-392041",
    "zip_code": "94107",
    "address": "742 Evergreen Terrace, San Francisco, CA 94107",
    "last4": "4829",
    "date_of_birth": "04/18/1988",
    "generic": "REF-90412",
}


def normalize_speaker_role(raw_role: str) -> str:
    """Normalizes arbitrary CCaaS speaker labels into Dialogflow participant roles."""
    s = (raw_role or "").strip().upper()
    if any(
        k in s
        for k in (
            "CUSTOMER",
            "USER",
            "CALLER",
            "CLIENT",
            "EXTERNAL",
            "BUYER",
            "MEMBER",
            "PATIENT",
            "SUBSCRIBER",
            "END_USER",
        )
    ):
        return "END_USER"
    if any(
        k in s
        for k in (
            "HUMAN_AGENT",
            "AGENT",
            "REP",
            "ADVISOR",
            "SUPPORT",
            "SPECIALIST",
            "INTERNAL",
            "OPERATOR",
            "ASSOCIATE",
        )
    ):
        return "HUMAN_AGENT"
    if any(k in s for k in ("BOT", "VIRTUAL", "IVA", "IVR", "WORKFLOW", "ACD")):
        return "VIRTUAL_AGENT"
    if any(k in s for k in ("SYSTEM", "CONTEXT", "CRM", "METADATA", "INGEST")):
        return "INGEST_CONTEXT"
    return "END_USER"


def is_low_signal_utterance(text: str) -> bool:
    """Returns True if an utterance is a pure greeting, acknowledgment, or hold filler."""
    cleaned = (text or "").strip()
    if not cleaned:
        return True
    if len(cleaned.split()) <= 6 and LOW_SIGNAL_UTTERANCE_RE.match(cleaned):
        return True
    return False


def _infer_redaction_category(full_text: str, match_span: tuple[int, int], token: str) -> str:
    """Infers the semantic category of a `[REDACTED]` placeholder from its token name and immediate context."""
    upper_tok = token.upper()
    if "EMAIL" in upper_tok:
        return "email"
    if any(k in upper_tok for k in ("PHONE", "TEL", "MOBILE", "CELL")):
        return "phone"
    if "ORDER" in upper_tok:
        return "order_id"
    if any(k in upper_tok for k in ("ACCOUNT", "ACCT", "MEMBER_ID", "POLICY")):
        return "account_id"
    if "ZIP" in upper_tok or "POSTAL" in upper_tok:
        return "zip_code"
    if "ADDRESS" in upper_tok or "STREET" in upper_tok:
        return "address"
    if any(k in upper_tok for k in ("SSN", "CARD", "LAST4", "PIN", "CVV")):
        return "last4"
    if any(k in upper_tok for k in ("DOB", "BIRTH")):
        return "date_of_birth"
    if "NAME" in upper_tok:
        return "name"

    start, end = match_span
    # Immediate preceding phrase (up to last clause separator or 28 chars) prevents earlier clauses from bleeding in
    immediate_before = full_text[max(0, start - 28) : start].lower()
    window_before = full_text[max(0, start - 50) : start].lower()
    window_after = full_text[end : min(len(full_text), end + 25)].lower()
    context = f"{immediate_before} [TOKEN] {window_after}"

    if any(k in context for k in ("email", "e-mail", "@", "send it to")):
        return "email"
    if any(
        k in immediate_before
        for k in ("order #", "order number", "order id", "order is", "order ", "tracking")
    ):
        return "order_id"
    if any(
        k in immediate_before
        for k in ("account #", "account number", "account id", "policy", "member id")
    ):
        return "account_id"
    if any(
        k in immediate_before
        for k in (
            "phone",
            "callback",
            "call me at",
            "number is",
            "number as",
            "reach me at",
            "cell",
            "mobile",
        )
    ):
        return "phone"
    if any(
        k in window_before
        for k in ("last 4", "last four", "ssn", "social security", "ending in", "digits")
    ):
        return "last4"
    if any(k in immediate_before for k in ("zip", "postal code")):
        return "zip_code"
    if any(
        k in window_before
        for k in ("shipping address", "mailing address", "live at", "street", "deliver to")
    ):
        return "address"
    if any(k in window_before for k in ("date of birth", "dob", "birthday", "born on")):
        return "date_of_birth"
    if any(
        k in immediate_before
        for k in (
            "my name is",
            "this is",
            "i am",
            "i'm",
            "speaking with",
            "thank you,",
            "thanks,",
            "for you,",
            "hello",
            "hi ",
            "mr.",
            "ms.",
            "mrs.",
        )
    ):
        return "name"
    return "generic"


def heal_redacted_placeholders(
    turns: list[dict[str, Any]],
    synthetic_profile: Optional[dict[str, str]] = None,
    client: Optional[CompanionAgentRestClient] = None,
    use_vertex_healer: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Stage 3: Replaces `[REDACTED]` / `[PII]` / `***` / `XXXX` spans with consistent synthetic values.

    Guarantees that if the caller provides a redacted email/order/name in an early turn and
    the agent repeats it in a later turn, both turns receive the exact same synthetic entity.
    """
    profile = dict(DEFAULT_SYNTHETIC_PROFILE)
    if synthetic_profile:
        profile.update(synthetic_profile)

    redacted_turn_indices: list[int] = []
    total_redactions_healed = 0
    categories_healed: dict[str, int] = {}

    for idx, t in enumerate(turns):
        text = str(t.get("text") or "")
        if REDACTION_PATTERN.search(text):
            redacted_turn_indices.append(idx)

    if not redacted_turn_indices:
        return turns, {
            "healed_turns": 0,
            "total_redactions_healed": 0,
            "categories_healed": {},
            "synthetic_entities_used": {},
        }

    # Optional Vertex AI Gemini healer if requested and client is available
    if use_vertex_healer and client is not None:
        try:
            prompt = (
                "You are a Contact Center Data Sanitizer & PII Healer.\n"
                "Replace all redacted placeholders ([REDACTED], [PII], ***, XXXX) in the following "
                "conversation turns with realistic, natural, internally consistent synthetic values.\n"
                "Return ONLY a JSON array of objects with `role` and `text` keys matching the input length.\n\n"
                f"Input JSON:\n{json.dumps([{'role': t['role'], 'text': t['text']} for t in turns], indent=2)}"
            )
            url = (
                f"https://{client.location}-aiplatform.googleapis.com/v1/projects/{client.project_id}"
                f"/locations/{client.location}/publishers/google/models/gemini-2.5-flash:generateContent"
            )
            resp = client.request(
                "POST",
                url,
                json_body={
                    "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
                },
            )
            cand_text = (
                (((resp.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0].get("text")
                or ""
            )
            healed_list = json.loads(cand_text)
            if isinstance(healed_list, list) and len(healed_list) == len(turns):
                out_turns = []
                for orig, healed in zip(turns, healed_list):
                    merged = dict(orig)
                    merged["text"] = str(healed.get("text") or orig["text"]).strip()
                    out_turns.append(merged)
                return out_turns, {
                    "healed_turns": len(redacted_turn_indices),
                    "total_redactions_healed": len(redacted_turn_indices),
                    "categories_healed": {"vertex_llm_healed": len(redacted_turn_indices)},
                    "synthetic_entities_used": profile,
                }
        except Exception:
            # Fall back cleanly to deterministic context-aware healer
            pass

    healed_turns: list[dict[str, Any]] = []
    used_entities: dict[str, str] = {}

    for t in turns:
        new_turn = dict(t)
        role = str(t.get("role") or "END_USER")
        text = str(t.get("text") or "")

        def _replacer(match: re.Match[str]) -> str:
            nonlocal total_redactions_healed
            token = match.group(0)
            cat = _infer_redaction_category(text, match.span(), token)
            total_redactions_healed += 1
            categories_healed[cat] = categories_healed.get(cat, 0) + 1

            if cat == "name":
                lower_before = text[: match.start()].lower()
                # If the agent introduces themselves ("Thank you for calling, my name is [REDACTED]")
                if role == "HUMAN_AGENT" and any(
                    p in lower_before for p in ("my name is", "this is", "i am", "i'm")
                ):
                    val = profile["agent_name"]
                    used_entities["agent_name"] = val
                    return val
                val = profile["customer_name"]
                used_entities["customer_name"] = val
                return val

            val = profile.get(cat, profile["generic"])
            used_entities[cat] = val
            return val

        new_turn["text"] = REDACTION_PATTERN.sub(_replacer, text)
        healed_turns.append(new_turn)

    return healed_turns, {
        "healed_turns": len(redacted_turn_indices),
        "total_redactions_healed": total_redactions_healed,
        "categories_healed": categories_healed,
        "synthetic_entities_used": used_entities,
    }


def _parse_stage1_json(raw_text: str) -> tuple[Optional[list[dict[str, Any]]], Optional[str]]:
    """Stage 1: Deterministic JSON schema fingerprinting for Genesys, Amazon Connect, CCAI, and generic JSON."""
    stripped = (raw_text or "").strip()
    if not (stripped.startswith("{") or stripped.startswith("[")):
        return None, None

    try:
        data = json.loads(stripped)
    except Exception:
        return None, None

    turns: list[dict[str, Any]] = []
    detected_format = "generic_json"

    if isinstance(data, dict):
        # 1. Genesys Cloud Speech & Text Analytics format
        if "transcripts" in data and isinstance(data["transcripts"], list):
            detected_format = "genesys_cloud"
            for tr in data["transcripts"]:
                for p in tr.get("phrases") or []:
                    purpose = str(p.get("participantPurpose") or "").lower()
                    if purpose in ("external", "customer", "end_user"):
                        role = "END_USER"
                    elif purpose in ("internal", "agent", "user"):
                        role = "HUMAN_AGENT"
                    else:
                        role = "VIRTUAL_AGENT"
                    txt = str(p.get("text") or p.get("decoratedText") or "").strip()
                    if txt:
                        turns.append({"role": role, "text": txt})

        # 2. Amazon Connect (Contact Lens) format
        elif "Transcript" in data and isinstance(data["Transcript"], list):
            detected_format = "amazon_connect_contact_lens"
            for item in data["Transcript"]:
                p_role = str(item.get("ParticipantId") or item.get("ParticipantRole") or "").upper()
                if "CUSTOMER" in p_role:
                    role = "END_USER"
                elif "AGENT" in p_role:
                    role = "HUMAN_AGENT"
                else:
                    role = "INGEST_CONTEXT"
                txt = str(item.get("Content") or "").strip()
                if txt:
                    turns.append({"role": role, "text": txt})

        # 3. Google Cloud CCAI / Dialogflow Insights `entries`
        elif "entries" in data and isinstance(data["entries"], list):
            detected_format = "google_ccai_entries"
            for entry in data["entries"]:
                role = normalize_speaker_role(str(entry.get("role") or entry.get("userId") or ""))
                txt = str(entry.get("text") or "").strip()
                if txt:
                    turns.append({"role": role, "text": txt})

        # 4. Existing aa-devkit dataset or `conversations` wrapper
        elif "conversations" in data and isinstance(data["conversations"], list) and data["conversations"]:
            detected_format = "aa_devkit_conversations"
            first_conv = data["conversations"][0]
            for item in first_conv.get("turns") or []:
                role = normalize_speaker_role(str(item.get("role") or item.get("speaker") or ""))
                txt = str(item.get("text") or "").strip()
                if txt:
                    turn_obj: dict[str, Any] = {"role": role, "text": txt}
                    if "expected" in item:
                        turn_obj["expected"] = item["expected"]
                    turns.append(turn_obj)

        else:
            # Generic dict wrapping a list under a standard key
            for key in ("turns", "messages", "utterances", "dialogue", "transcript", " script", "script"):
                k_clean = key.strip()
                if k_clean in data and isinstance(data[k_clean], list):
                    data = data[k_clean]
                    break

    if isinstance(data, list) and not turns:
        for item in data:
            if not isinstance(item, dict):
                continue
            raw_role = (
                item.get("role")
                or item.get("speaker")
                or item.get("participant")
                or item.get("author")
                or item.get("sender")
                or "END_USER"
            )
            raw_txt = (
                item.get("text")
                or item.get("utterance")
                or item.get("content")
                or item.get("message")
                or item.get("body")
                or ""
            )
            txt = str(raw_txt).strip()
            if txt:
                turn_obj = {"role": normalize_speaker_role(str(raw_role)), "text": txt}
                if isinstance(item.get("expected"), dict):
                    turn_obj["expected"] = item["expected"]
                turns.append(turn_obj)

    if turns:
        return turns, detected_format
    return None, None


def _parse_stage2_csv_or_regex(raw_text: str) -> tuple[list[dict[str, Any]], str]:
    """Stage 2: CSV/TSV parser and timestamped call-log regex heuristics."""
    stripped = (raw_text or "").strip()
    if not stripped:
        return [], "empty"

    lines = stripped.splitlines()

    # 2A. Try CSV/TSV if header row contains speaker & text columns
    if len(lines) >= 2 and ("," in lines[0] or "\t" in lines[0]):
        try:
            delim = "\t" if "\t" in lines[0] else ","
            reader = csv.DictReader(io.StringIO(stripped), delimiter=delim)
            if reader.fieldnames:
                headers = {h.strip().lower(): h for h in reader.fieldnames if h}
                role_col = next(
                    (
                        headers[k]
                        for k in ("role", "speaker", "participant", "sender", "author", "actor", "party")
                        if k in headers
                    ),
                    None,
                )
                text_col = next(
                    (
                        headers[k]
                        for k in ("text", "utterance", "content", "message", "transcript", "body", "line")
                        if k in headers
                    ),
                    None,
                )
                if role_col and text_col:
                    csv_turns: list[dict[str, Any]] = []
                    for row in reader:
                        r_val = row.get(role_col) or ""
                        t_val = (row.get(text_col) or "").strip()
                        if t_val:
                            csv_turns.append({"role": normalize_speaker_role(r_val), "text": t_val})
                    if csv_turns:
                        return csv_turns, "csv_tsv"
        except Exception:
            pass

    # 2B. Timestamped call log regex heuristics
    # Matches: "[00:01:12] Agent (Sarah): Hello..." or "12:01 PM - Customer: Hi..." or "Caller: ..."
    line_pattern = re.compile(
        r"^(?:\[?\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?(?:\s*[AP]M)?\]?\s*[-–—]?\s*)?"
        r"(?:\[?(Customer|Caller|User|Client|End[ _]?User|Agent|Human[ _]?Agent|Rep|Advisor|Support|"
        r"Specialist|Associate|Bot|Virtual[ _]?Agent|IVA|IVR|System|Context)"
        r"(?:\s*\([^)]+\))?\]?)"
        r"\s*[:\-–—]\s*(.+)$",
        re.IGNORECASE,
    )

    regex_turns: list[dict[str, Any]] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        m = line_pattern.match(line)
        if m:
            role = normalize_speaker_role(m.group(1))
            txt = m.group(2).strip()
            if txt:
                regex_turns.append({"role": role, "text": txt})
        elif regex_turns:
            # Multi-line continuation of the previous speaker's turn
            regex_turns[-1]["text"] += " " + line

    if regex_turns:
        return regex_turns, "timestamped_call_log"

    # Fallback: treat each non-empty line as alternating END_USER / HUMAN_AGENT turns
    fallback_turns: list[dict[str, Any]] = []
    for idx, raw_line in enumerate(lines):
        line = raw_line.strip()
        if line:
            role = "END_USER" if idx % 2 == 0 else "HUMAN_AGENT"
            fallback_turns.append({"role": role, "text": line})
    return fallback_turns, "plain_text_alternating"


def merge_consecutive_speaker_turns(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merges consecutive turns from the same `END_USER` or `HUMAN_AGENT` speaker."""
    merged: list[dict[str, Any]] = []
    for t in turns:
        role = str(t.get("role") or "END_USER")
        text = str(t.get("text") or "").strip()
        if not text:
            continue
        if (
            merged
            and merged[-1]["role"] == role
            and role in ("END_USER", "HUMAN_AGENT")
            and not t.get("expected")
        ):
            merged[-1]["text"] = f"{merged[-1]['text']} {text}".strip()
        else:
            merged.append(dict(t))
    return merged


def import_transcript(
    raw_text: str,
    conversation_id: str = "imported-conv-001",
    description: str = "Imported and PII-healed contact center transcript",
    agent_mode: str = "TRANSCRIPT",
    merge_consecutive: bool = True,
    heal_pii: bool = True,
    synthetic_profile: Optional[dict[str, str]] = None,
    client: Optional[CompanionAgentRestClient] = None,
    use_vertex_healer: bool = False,
) -> dict[str, Any]:
    """Runs the 3-Stage Universal Transcript Importer & PII Healer and returns an FR-4.2 dataset dict."""
    turns, detected_format = _parse_stage1_json(raw_text)
    if not turns:
        turns, detected_format = _parse_stage2_csv_or_regex(raw_text)

    if merge_consecutive:
        turns = merge_consecutive_speaker_turns(turns)

    healer_stats: dict[str, Any] = {
        "healed_turns": 0,
        "total_redactions_healed": 0,
        "categories_healed": {},
        "synthetic_entities_used": {},
    }
    if heal_pii:
        turns, healer_stats = heal_redacted_placeholders(
            turns=turns,
            synthetic_profile=synthetic_profile,
            client=client,
            use_vertex_healer=use_vertex_healer,
        )

    formatted_turns: list[dict[str, Any]] = []
    mode_upper = (agent_mode or "TRANSCRIPT").strip().upper()

    for idx, t in enumerate(turns, start=1):
        role = str(t.get("role") or "END_USER")
        text = str(t.get("text") or "").strip()
        expected = dict(t.get("expected") or {})

        if "must_suppress" not in expected:
            expected["must_suppress"] = is_low_signal_utterance(text)
        expected.setdefault("expected_guidance_cards", [])
        expected.setdefault("expected_tools", [])
        expected.setdefault("expected_entities", {})
        expected.setdefault("not_expected_entities", {})

        if mode_upper == "SUGGESTION" and role == "HUMAN_AGENT":
            text = (
                "[Follow the Companion Agent's suggestion or guidance card if available; "
                "otherwise respond naturally to progress the conversation.]"
            )

        formatted_turns.append(
            {
                "turn_id": idx,
                "role": role,
                "text": text,
                "expected": expected,
            }
        )

    return {
        "schemaVersion": "1.0",
        "importMetadata": {
            "detectedFormat": detected_format,
            "agentMode": mode_upper,
            "mergedConsecutiveTurns": merge_consecutive,
            "piiHealer": healer_stats,
        },
        "conversations": [
            {
                "conversation_id": conversation_id,
                "description": description,
                "ingested_context": {},
                "turns": formatted_turns,
            }
        ],
    }


def import_transcript_file(
    input_path: str | Path,
    conversation_id: Optional[str] = None,
    description: Optional[str] = None,
    agent_mode: str = "TRANSCRIPT",
    merge_consecutive: bool = True,
    heal_pii: bool = True,
    synthetic_profile: Optional[dict[str, str]] = None,
    client: Optional[CompanionAgentRestClient] = None,
    use_vertex_healer: bool = False,
) -> dict[str, Any]:
    """Reads a transcript file (JSON, CSV, TSV, or TXT) and converts it to the FR-4.2 evaluation schema."""
    p = Path(input_path).expanduser().resolve()
    raw_text = p.read_text(encoding="utf-8")
    conv_id = conversation_id or f"imported-{p.stem.replace('_', '-')}"
    desc = description or f"Imported from {p.name} via 3-Stage Universal Transcript Importer"
    return import_transcript(
        raw_text=raw_text,
        conversation_id=conv_id,
        description=desc,
        agent_mode=agent_mode,
        merge_consecutive=merge_consecutive,
        heal_pii=heal_pii,
        synthetic_profile=synthetic_profile,
        client=client,
        use_vertex_healer=use_vertex_healer,
    )
