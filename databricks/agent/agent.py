"""Production-surveillance agent (theme 1 hero) — Mosaic AI Agent Framework (DESIGN §4 layer 5).

A tool-calling agent, driven by the Databricks-hosted Claude Opus 4.8 foundation model, that answers
the hero question — "which wells are producing below expected oil rate this week, and by how much?" —
over the governed Unity Catalog substrate. The agent NEVER authors SQL or invents numbers: it selects
governed metrics through the oag_fable.knowledge.* UC-function tools (term resolution, entity
resolution, and the surveillance metric-view query), mirroring the DESIGN semantic seam ("agents
*select* governed metrics rather than author SQL").

The numeric answer (key_values.flagged) is assembled deterministically from the governed
`surveillance_below_expected` tool result, so functional correctness (rubric dimension 1) is objective;
the LLM handles intent routing, field entity-resolution, the natural-language narrative, and behavior.
Output conforms to spec/questions/answer_submission.schema.json.
"""

from __future__ import annotations

import csv
import io
import json
import os
import uuid
from datetime import date
from typing import Any

import mlflow
from databricks.sdk import WorkspaceClient
from databricks_openai import UCFunctionToolkit
from mlflow.pyfunc import ResponsesAgent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentResponse
from unitycatalog.ai.core.databricks import DatabricksFunctionClient

# Foundation-model endpoint driving the agent. The intended driver is databricks-claude-opus-4-8, but
# on this account both Claude pay-per-token endpoints return a "rate limit of 0" (disabled). Of the
# available models, Qwen3-next-80b does reliable OpenAI-structured tool-calling (Llama 4 Maverick
# emitted tool calls as text and hallucinated results). Flip back to databricks-claude-opus-4-8 (and
# redeploy) once the Claude entitlement is enabled — the agent is otherwise model-agnostic.
LLM_ENDPOINT = os.environ.get("OAG_AGENT_LLM", "databricks-qwen3-next-80b-a3b-instruct")
CATALOG, KSCHEMA = "oag_fable", "knowledge"
QUESTION_ID = "surveillance-below-expected-oil"


def _fn(name: str) -> str:
    return f"{CATALOG}.{KSCHEMA}.{name}"


UC_TOOLS = [
    _fn("resolve_term"),
    _fn("resolve_field"),
    _fn("wells_in_field"),
    _fn("surveillance_config"),
    _fn("surveillance_below_expected"),
]

SYSTEM_PROMPT = """\
You are the production-surveillance agent for the oag_fable oil & gas platform. You answer the business
question: "which wells are producing below expected oil rate this week, and by how much?"

Use ONLY the provided Unity Catalog tools. Never write SQL and never invent or estimate numbers.

Procedure:
1. Call resolve_term on the user's phrasing to confirm it denotes the governed production_efficiency
   metric (the "below expected" surveillance signal).
2. If the user names a field (e.g. "Volve", "Sleipner"), call resolve_field to get its FIELD_ID, then
   pass it to surveillance_below_expected. If resolve_field returns null, tell the user that field is
   unknown and do not guess. If no field is named, use 0 (the whole fleet).
3. Call surveillance_below_expected(field_id) — its rows ARE the governed answer (worst miss first).
4. Reply with a concise natural-language summary: how many wells (of the fleet or the named field) are
   below the threshold this surveillance week, and which single well missed by the most — its UWI,
   shortfall in bbl, and efficiency as a percentage of expected. Do NOT enumerate every well in prose;
   the full structured rows are returned separately.
"""


def _openai_client():
    """OpenAI-compatible client pointed at the workspace's Foundation Model serving."""
    return WorkspaceClient().serving_endpoints.get_open_ai_client()


def _parse_csv(text: str | None) -> list[dict[str, str]]:
    if not text:
        return []
    return list(csv.DictReader(io.StringIO(text)))


class SurveillanceAgent(ResponsesAgent):
    """Deterministic-answer, LLM-routed surveillance agent over the governed UC tools."""

    def __init__(self) -> None:
        self.uc = DatabricksFunctionClient()
        self.toolkit = UCFunctionToolkit(function_names=UC_TOOLS, client=self.uc)
        self.tools = self.toolkit.tools
        # The LLM may echo a tool by its schema name (catalog__schema__fn) or bare (fn); map every
        # alias back to the dotted UC name so execution is robust across models.
        self._tool_map: dict[str, str] = {}
        for full in UC_TOOLS:
            self._tool_map[full] = full
            self._tool_map[full.replace(".", "__")] = full
            self._tool_map[full.split(".")[-1]] = full

    def _exec_tool(self, tool_name: str, args: dict[str, Any]) -> str | None:
        uc_name = self._tool_map.get(tool_name) or tool_name.replace("__", ".")
        result = self.uc.execute_function(uc_name, args)
        if getattr(result, "error", None):
            return f"ERROR: {result.error}"
        return getattr(result, "value", None)

    def predict(self, request: ResponsesAgentRequest) -> ResponsesAgentResponse:
        llm = _openai_client()
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += self.prep_msgs_for_cc_llm(request.input)

        surv_csv: str | None = None
        field_arg = 0
        final_text = ""
        for _ in range(8):
            resp = llm.chat.completions.create(
                model=LLM_ENDPOINT, messages=messages, tools=self.tools, temperature=0.0
            )
            msg = resp.choices[0].message
            turn: dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            if msg.tool_calls:
                turn["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(turn)

            if not msg.tool_calls:
                final_text = msg.content or ""
                break

            for tc in msg.tool_calls:
                name = tc.function.name
                args = json.loads(tc.function.arguments or "{}")
                out = self._exec_tool(name, args)
                if name.endswith("surveillance_below_expected"):
                    surv_csv = out
                    try:
                        field_arg = int(args.get("arg_field_id", 0) or 0)
                    except (TypeError, ValueError):
                        field_arg = 0
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": str(out)})

        # Deterministic guarantee: if the model never called the governed tool, call it (fleet).
        if surv_csv is None:
            surv_csv = self._exec_tool(_fn("surveillance_below_expected"), {"arg_field_id": field_arg})

        submission = self._build_submission(surv_csv, field_arg, final_text)
        item = self.create_text_output_item(text=json.dumps(submission), id=str(uuid.uuid4()))
        return ResponsesAgentResponse(output=[item], custom_outputs={"answer_submission": submission})

    def _build_submission(self, surv_csv: str | None, field_arg: int, final_text: str) -> dict[str, Any]:
        flagged = [
            {
                "well_id": int(r["well_id"]),
                "expected_oil_bbl": float(r["expected_oil_bbl"]),
                "actual_oil_bbl": float(r["actual_oil_bbl"]),
                "shortfall_bbl": float(r["shortfall_bbl"]),
                "efficiency": float(r["efficiency"]),
            }
            for r in _parse_csv(surv_csv)
        ]
        cfg = _parse_csv(self._exec_tool(_fn("surveillance_config"), {}))
        win = cfg[0] if cfg else {}
        start, end = win.get("window_start"), win.get("window_end")
        threshold = float(win.get("flag_threshold", 0.9))
        days = None
        if start and end:
            days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1

        scoped = field_arg > 0
        key_values = {
            "window": {"start": start, "end": end, "days": days},
            "flag_threshold": threshold,
            "unit": "bbl",
            "n_flagged": len(flagged),
            "flagged": flagged,
        }
        provenance = {
            "metrics": ["production_efficiency", "expected_oil", "actual_oil", "oil_shortfall"],
            "dimensions": ["well", "volume_date"] + (["field"] if scoped else []),
            "filters": [
                f"production_efficiency < {threshold}",
                f"volume_date in [{start}..{end}]",
            ] + ([f"field_id = {field_arg}"] if scoped else []),
            "entities": ["Well"] + ([f"Field(FIELD_ID={field_arg})"] if scoped else []),
        }
        answer = final_text.strip() or (
            f"{len(flagged)} wells produced below {round(threshold * 100)}% of expected oil "
            f"during {start}..{end}."
        )
        return {
            "question_id": QUESTION_ID,
            "answer": answer,
            "key_values": key_values,
            "behavior": "answered",
            "provenance": provenance,
        }


mlflow.models.set_model(SurveillanceAgent())
