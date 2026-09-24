"""
ProjectState — the single source of truth shared across sub-agents in a conversation.

The orchestrator owns one instance per conversation. After every successful
sub-agent run the agent's summary is distilled (cheap structured-output call on
the lite model) and merged in; before every delegation the accumulated brief is
prepended to the sub-agent's context IN CODE, so downstream agents always see
the same phases, timeline, team and figures regardless of what the orchestrator
model chose to type.

`to_brief()` renders the exact PROJECT BRIEF layout the agent prompts already
treat as their source of truth, so no agent prompt needed rewriting.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict

log = logging.getLogger(__name__)

# Fields a later agent may legitimately revise (e.g. Financial refines the budget
# Planning only estimated). Everything else is first-writer-wins.
REVISABLE_BY = {
    "budget": {"Financial Manager Agent"},
    "milestones": {"Project Planning Agent"},
    "team_roles": {"Project Orchestration Agent", "Project Planning Agent"},
}


@dataclass
class Phase:
    name: str
    duration: str = ""
    start: str = ""
    end: str = ""


@dataclass
class Budget:
    optimistic_total: str = ""
    most_likely_total: str = ""
    pessimistic_total: str = ""
    contingency_rate: str = ""
    grand_total: str = ""

    def is_empty(self) -> bool:
        return not any(asdict(self).values())


@dataclass
class FileRecord:
    filename: str
    doc_type: str          # word | excel | powerpoint
    produced_by: str


@dataclass
class ProjectState:
    project_name: str = ""
    total_duration: str = ""
    start_date: str = ""
    end_date: str = ""
    phases: list[Phase] = field(default_factory=list)
    team_roles: dict[str, str] = field(default_factory=dict)      # role -> headcount
    milestones: dict[str, str] = field(default_factory=dict)      # milestone -> date
    budget: Budget = field(default_factory=Budget)
    assumptions: list[str] = field(default_factory=list)
    files: list[FileRecord] = field(default_factory=list)
    contributors: list[str] = field(default_factory=list)

    # ── queries ──────────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        return not (self.project_name or self.total_duration or self.phases or self.team_roles
                    or self.milestones or not self.budget.is_empty() or self.files)

    def has_brief(self) -> bool:
        """True once there is something a downstream agent must stay consistent with."""
        return bool(self.project_name or self.total_duration or self.phases
                    or self.team_roles or not self.budget.is_empty())

    # ── rendering ────────────────────────────────────────────────────────────

    def to_brief(self) -> str:
        """The PROJECT BRIEF block every agent prompt is written to obey."""
        line = "─" * 64
        out = [line, "PROJECT BRIEF (source of truth — accumulated from prior agents)"]
        out.append(f"Project Name: {self.project_name or 'TBD'}")
        dur = self.total_duration or "TBD"
        if self.start_date or self.end_date:
            dur += f" ({self.start_date or '?'} → {self.end_date or '?'})"
        out.append(f"Total Duration: {dur}")
        out.append("Phases:")
        if self.phases:
            for i, p in enumerate(self.phases, 1):
                span = f" ({p.start} → {p.end})" if (p.start or p.end) else ""
                dur_txt = f" — {p.duration}" if p.duration else ""
                out.append(f"  {i}. {p.name}{dur_txt}{span}")
        else:
            out.append("  TBD")
        roles = ", ".join(f"{r}: {n}" for r, n in self.team_roles.items()) or "TBD"
        out.append(f"Team Roles: {roles}")
        out.append("Key Milestones:")
        if self.milestones:
            for m, d in self.milestones.items():
                out.append(f"  - {m}: {d}")
        else:
            out.append("  TBD")
        b = self.budget
        out.append("Budget:")
        if b.is_empty():
            out.append("  TBD (not yet calculated)")
        else:
            out.append(f"  Optimistic Total:    {b.optimistic_total or 'TBD'}")
            out.append(f"  Most Likely Total:   {b.most_likely_total or 'TBD'}")
            out.append(f"  Pessimistic Total:   {b.pessimistic_total or 'TBD'}")
            out.append(f"  Contingency Rate:    {b.contingency_rate or 'TBD'}")
            out.append(f"  Grand Total (ML):    {b.grand_total or 'TBD'}")
        if self.assumptions:
            out.append("Assumptions:")
            out += [f"  - {a}" for a in self.assumptions]
        if self.files:
            out.append("Files produced so far (read the relevant one with read_output_file before "
                       "restating its figures):")
            out += [f"  - {f.filename} ({f.doc_type}, by {f.produced_by})" for f in self.files]
        out.append(line)
        return "\n".join(out)

    # ── mutation ─────────────────────────────────────────────────────────────

    def add_files(self, filenames: list[str], produced_by: str) -> None:
        ext = {".docx": "word", ".xlsx": "excel", ".pptx": "powerpoint"}
        known = {f.filename for f in self.files}
        for name in filenames:
            base = name.replace("\\", "/").rsplit("/", 1)[-1]
            if base in known:
                continue
            doc_type = ext.get(base[base.rfind("."):].lower(), "file")
            self.files.append(FileRecord(base, doc_type, produced_by))
            known.add(base)

    def merge(self, delta: dict, source: str) -> list[str]:
        """Merge an extracted delta. First writer wins, unless `source` is allowed
        to revise that field. Returns the names of fields that changed."""
        changed: list[str] = []

        def take(field_name: str, value, is_set: bool) -> bool:
            allowed = not is_set or source in REVISABLE_BY.get(field_name, set())
            return bool(value) and allowed

        if take("project_name", delta.get("project_name"), bool(self.project_name)):
            self.project_name = delta["project_name"].strip(); changed.append("project_name")
        if take("total_duration", delta.get("total_duration"), bool(self.total_duration)):
            self.total_duration = delta["total_duration"].strip(); changed.append("total_duration")
        for k in ("start_date", "end_date"):
            if take(k, delta.get(k), bool(getattr(self, k))):
                setattr(self, k, delta[k].strip()); changed.append(k)

        phases = delta.get("phases") or []
        if take("phases", phases, bool(self.phases)):
            self.phases = [Phase(**{kk: str(p.get(kk, "") or "") for kk in ("name", "duration", "start", "end")})
                           for p in phases if p.get("name")]
            changed.append("phases")

        roles = delta.get("team_roles") or []
        if take("team_roles", roles, bool(self.team_roles)):
            self.team_roles = {r["role"]: str(r.get("headcount", "") or "") for r in roles if r.get("role")}
            changed.append("team_roles")

        ms = delta.get("milestones") or []
        if take("milestones", ms, bool(self.milestones)):
            self.milestones = {m["name"]: str(m.get("date", "") or "") for m in ms if m.get("name")}
            changed.append("milestones")

        b = delta.get("budget") or {}
        if take("budget", any(b.values()) if isinstance(b, dict) else False, not self.budget.is_empty()):
            self.budget = Budget(**{k: str(b.get(k, "") or "") for k in asdict(Budget())})
            changed.append("budget")

        for a in delta.get("assumptions") or []:
            a = str(a).strip()
            if a and a not in self.assumptions:
                self.assumptions.append(a); changed.append("assumptions")

        if source not in self.contributors:
            self.contributors.append(source)
        return changed

    def to_dict(self) -> dict:
        return asdict(self)


# ── Extraction (lite model, structured output) ────────────────────────────────

DELTA_SCHEMA = {
    "type": "object",
    "properties": {
        "project_name": {"type": "string"},
        "total_duration": {"type": "string", "description": "e.g. '9 months'"},
        "start_date": {"type": "string"},
        "end_date": {"type": "string"},
        "phases": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "duration": {"type": "string"},
            "start": {"type": "string"}, "end": {"type": "string"}}, "required": ["name"]}},
        "team_roles": {"type": "array", "items": {"type": "object", "properties": {
            "role": {"type": "string"}, "headcount": {"type": "string"}}, "required": ["role"]}},
        "milestones": {"type": "array", "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "date": {"type": "string"}}, "required": ["name"]}},
        "budget": {"type": "object", "properties": {
            "optimistic_total": {"type": "string"}, "most_likely_total": {"type": "string"},
            "pessimistic_total": {"type": "string"}, "contingency_rate": {"type": "string"},
            "grand_total": {"type": "string"}}},
        "assumptions": {"type": "array", "items": {"type": "string"}},
    },
}

EXTRACT_PROMPT = """You extract project facts from a specialist agent's report.

Return ONLY facts the report states explicitly: project name, total duration and
dates, the list of phases with their durations/dates, team roles with headcounts,
key milestones with dates, budget totals (optimistic / most likely / pessimistic,
contingency rate, grand total) and any stated assumptions. Keep the exact figures
and wording used in the report (e.g. "$276,000", "9 months"). Omit every field the
report does not state — never guess or fill in.

AGENT: {agent}
REPORT:
{text}
"""


def extract_delta(agent_name: str, text: str, client=None, model: str | None = None) -> dict:
    """One cheap structured-output call. Returns {} on any failure — the caller
    must never let extraction break a run."""
    if not text or not text.strip():
        return {}
    try:
        from google import genai
        from google.genai import types
        from config import GEMINI_API_KEY, MODEL_LITE
        from agents.base_agent import _generate_with_retry, make_thinking_config
        client = client or genai.Client(api_key=GEMINI_API_KEY)
        model = model or MODEL_LITE
        resp = _generate_with_retry(
            client, model,
            EXTRACT_PROMPT.format(agent=agent_name, text=text[:20000]),
            types.GenerateContentConfig(
                response_mime_type="application/json",
                response_json_schema=DELTA_SCHEMA,
                thinking_config=make_thinking_config(model, "select"),
                max_output_tokens=4000,
            ),
            max_retries=2,
        )
        data = json.loads(resp.text or "{}")
        return data if isinstance(data, dict) else {}
    except Exception as e:
        log.warning("project-state extraction failed for %s: %s", agent_name, e)
        return {}
