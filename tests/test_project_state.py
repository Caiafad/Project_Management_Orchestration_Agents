from project_state import ProjectState, Budget


PLANNING = "Project Planning Agent"
FINANCIAL = "Financial Manager Agent"
SCOPE = "Scope Definition Agent"

PLAN_DELTA = {
    "project_name": "NovaBank App",
    "total_duration": "9 months",
    "start_date": "Jan 6 2027",
    "end_date": "Oct 1 2027",
    "phases": [{"name": "Discovery", "duration": "2 months"},
               {"name": "Build", "duration": "5 months"}],
    "team_roles": [{"role": "Mobile Engineers", "headcount": "4"}],
    "milestones": [{"name": "Go-live", "date": "Oct 1 2027"}],
    "assumptions": ["15% contingency"],
}


def planned() -> ProjectState:
    s = ProjectState()
    s.merge(PLAN_DELTA, PLANNING)
    return s


def test_empty_state_has_no_brief():
    assert ProjectState().has_brief() is False


def test_merge_populates_and_brief_renders_facts():
    s = planned()
    assert s.has_brief()
    brief = s.to_brief()
    for fragment in ("NovaBank App", "9 months", "Discovery", "Mobile Engineers: 4",
                     "Go-live: Oct 1 2027", "15% contingency"):
        assert fragment in brief


def test_first_writer_wins_for_phases():
    s = planned()
    changed = s.merge({"phases": [{"name": "Something else"}]}, SCOPE)
    assert "phases" not in changed
    assert [p.name for p in s.phases] == ["Discovery", "Build"]


def test_financial_may_revise_budget_others_may_not():
    s = planned()
    s.merge({"budget": {"most_likely_total": "$1.0M"}}, SCOPE)
    assert s.budget.most_likely_total == "$1.0M", "first budget writer should win"

    changed = s.merge({"budget": {"most_likely_total": "$1.2M", "grand_total": "$1.38M"}}, FINANCIAL)
    assert "budget" in changed
    assert s.budget.most_likely_total == "$1.2M"

    s.merge({"budget": {"most_likely_total": "$9.9M"}}, SCOPE)
    assert s.budget.most_likely_total == "$1.2M", "non-financial agent must not overwrite budget"


def test_unknown_fields_render_as_tbd_not_placeholders():
    brief = ProjectState().to_brief()
    assert "TBD" in brief
    assert "[" not in brief


def test_add_files_dedupes_and_strips_paths():
    s = ProjectState()
    s.add_files(["C:/out/Plan.xlsx", "Plan.xlsx", "out/Plan.docx"], PLANNING)
    assert [f.filename for f in s.files] == ["Plan.xlsx", "Plan.docx"]
    assert [f.doc_type for f in s.files] == ["excel", "word"]
    assert s.files[0].produced_by == PLANNING


def test_files_appear_in_brief_for_downstream_agents():
    s = planned()
    s.add_files(["NovaBank_Plan.xlsx"], PLANNING)
    brief = s.to_brief()
    assert "NovaBank_Plan.xlsx" in brief and "read_output_file" in brief


def test_assumptions_do_not_duplicate():
    s = planned()
    s.merge({"assumptions": ["15% contingency", "No third-party licensing"]}, FINANCIAL)
    assert s.assumptions == ["15% contingency", "No third-party licensing"]


def test_empty_budget_detection():
    assert Budget().is_empty()
    assert not Budget(grand_total="$1").is_empty()


def test_contributors_recorded():
    s = planned()
    s.merge({}, FINANCIAL)
    assert s.contributors == [PLANNING, FINANCIAL]
