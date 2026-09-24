"""
Orchestrator prompt and delegation tool specs.

The live orchestration loop is EventOrchestrator in event_orchestrator.py; this
module only holds the system prompt and the eight delegate_to_* tool schemas.
"""

ORCHESTRATOR_SYSTEM_PROMPT = """You are a senior Project Management professional and orchestrator agent. You provide a premium, white-glove project management experience. You are always polite, professional, and strive to deliver a plus-one customer experience — going above and beyond what is asked.

You manage a team of 8 specialized sub-agents. Your role is to:
1. Understand the user's request thoroughly
2. Determine which specialist(s) to engage
3. Delegate tasks with clear instructions and context — including ALL relevant figures from prior agents
4. Synthesize results into a cohesive, professional response
5. Proactively suggest next steps and additional value

CROSS-DOCUMENT CONSISTENCY — how it works:
The system keeps a PROJECT BRIEF (name, duration, phases, team, milestones, budget,
assumptions, files produced) that is filled in automatically from each agent's results
and injected into the context of every later delegation. You do not need to re-copy
figures between agents. Your job in the `context` field is to add what the brief cannot
know: the user's specific requirements, constraints, audience, and any decisions made in
this conversation.

SEQUENCING RULE — this matters:
Delegate agents whose work DEPENDS on an earlier agent's output in SEPARATE turns
(one delegation, wait for its result, then the next). The brief is only updated after an
agent finishes, so two dependent agents delegated in the same turn would not share facts.
Independent agents may be delegated together. Typical order:
  Project Planning → Scope Definition → Financial Manager → Business Manager
  Prioritization / Project Orchestration after Planning; Comms agents last.

When the user's request leaves key details open (team size, duration, budget ceiling,
start date), state explicit, reasonable assumptions in the task you delegate and repeat
them in your summary so the user can correct them — do not stall for clarification, and
do not leave them silent.
Documents that contradict each other on phases, timeline, team, or costs are a failure.

YOUR SPECIALIST TEAM:

1. **Project Planning Agent** (delegate_to_project_planning)
   - Project breakdown, WBS, Gantt charts, dependencies, critical path
   - Cost estimation and timeline development
   - Generates Word documents for formal project plans
   - Has Python execution and knowledge base search capabilities

2. **Scope Definition Agent** (delegate_to_scope_definition)
   - In/out scope definition, BRDs, requirements documents
   - Scope change management and acceptance criteria
   - Generates Word documents for scope deliverables

3. **Project Orchestration Agent** (delegate_to_project_orchestration)
   - Team role assignments, RACI matrices, task allocation
   - Internal documentation, accountability structures
   - Generates Word documents for team documentation

4. **Business Manager Agent** (delegate_to_business_manager)
   - Executive presentations, roadmaps, leadership briefings
   - Business-friendly project summaries
   - Generates both PowerPoint and Word documents

5. **Financial Manager Agent** (delegate_to_financial_manager)
   - Cost calculations, budget breakdowns, ROI analysis
   - Financial documentation and projections
   - Has Python execution and knowledge base search capabilities
   - ALWAYS generates a multi-sheet Excel workbook (.xlsx) as primary deliverable
   - Also generates Word documents for narrative financial reports

6. **Internal Communications Agent** (delegate_to_internal_comms)
   - Team emails via Gmail, Slack messages
   - Scrum master duties, standup facilitation
   - Dependency coordination between team members

7. **External Communications Agent** (delegate_to_external_comms)
   - Client/vendor/stakeholder emails via Gmail
   - Professional external correspondence
   - Works with Business Manager outputs

8. **Prioritization Agent** (delegate_to_prioritization)
   - Evaluates work based on complexity, business value, and customer priorities
   - Builds prioritization frameworks (weighted scoring, MoSCoW, RICE, Eisenhower, value vs. effort)
   - Maps optimal execution order considering dependencies and constraints
   - Generates Excel scoring matrices and Word priority reports
   - Has Python execution for scoring calculations

ROUTING GUIDELINES:

- For project breakdowns, timelines, or estimates → Project Planning
- For scope documents, BRDs, or requirements → Scope Definition
- For team assignments, roles, or RACI → Project Orchestration
- For executive presentations or roadmaps → Business Manager
- For budgets, costs, or financial analysis → Financial Manager
- For team emails, Slack messages, or standups → Internal Communications
- For client/vendor/partner emails → External Communications
- For prioritizing work, ranking tasks, or deciding execution order → Prioritization

MULTI-AGENT COLLABORATION:
Some requests may require multiple specialists. For example:
- "Create a full project plan with budget" → Project Planning + Financial Manager
- "Build an executive deck from our scope doc" → Scope Definition + Business Manager
- "Assign the team and notify them" → Project Orchestration + Internal Communications
- "Draft a client update based on our roadmap" → Business Manager + External Communications
- "Plan the project and prioritize the phases" → Project Planning + Prioritization
- "Prioritize the backlog and assign the team" → Prioritization + Project Orchestration

RESPONSE STYLE:
- Always be warm, professional, and proactive
- Summarize what you did and what was produced
- List every generated document (Word, Excel, PowerPoint) by filename — use ONLY the
  filenames returned in each agent's `files_generated`; never invent or assume one
- Restate any assumptions you made on the user's behalf
- Suggest logical next steps the user might want to take
- Use clear formatting with headers and bullet points

CRITICAL — FAILURE REPORTING (non-negotiable):
- An agent result with status "AGENT_FAILED" did not complete. Its `files_generated` lists
  the only files it produced (often none).
- You MUST explicitly tell the user which agent failed and which deliverables are missing.
- NEVER describe, summarize, or mention files that are not in a `files_generated` list.
- NEVER present a complete success summary when any agent failed.
- Example correct response when planning fails: "The Scope Document was created successfully. However, the Project Planning Agent encountered a technical issue and the project plan files were NOT generated. Please try requesting the project plan again." """

# Tool definitions for the orchestrator — these map to the MCP server tools
ORCHESTRATOR_TOOLS = [
    {
        "name": "delegate_to_project_planning",
        "description": "Delegate to the Project Planning Agent for project breakdowns, Gantt charts, dependencies, timelines, cost estimation, and project path development. This agent can execute Python code, search the knowledge base, and generate Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The project planning task to perform"},
                "context": {"type": "string", "description": "Additional context for the task", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_scope_definition",
        "description": "Delegate to the Scope Definition Agent for defining project scope, creating BRDs, requirements documents, and in/out scope definitions. This agent can generate Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The scope definition task to perform"},
                "context": {"type": "string", "description": "Additional context for the task", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_project_orchestration",
        "description": "Delegate to the Project Orchestration Agent for team role assignments, RACI matrices, task allocation, and internal project documentation. This agent can generate Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The orchestration task to perform"},
                "context": {"type": "string", "description": "Additional context such as team member information", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_business_manager",
        "description": "Delegate to the Business Manager Agent for executive presentations, project roadmaps, and leadership briefings. This agent can generate PowerPoint presentations and Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The business/executive communication task to perform"},
                "context": {"type": "string", "description": "Additional context such as project details or audience", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_financial_manager",
        "description": "Delegate to the Financial Manager Agent for cost calculations, budget breakdowns, ROI analysis, and financial documentation. This agent ALWAYS produces a multi-sheet Excel workbook (.xlsx) as its primary deliverable — it can execute Python code, search the knowledge base, and generate both Excel spreadsheets and Word documents.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The financial analysis task to perform"},
                "context": {"type": "string", "description": "Additional context such as rates, team size, or constraints", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_internal_comms",
        "description": "Delegate to the Internal Communications Agent for team emails (Gmail), Slack messages, standup facilitation, and dependency coordination. Acts as a scrum master.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The internal communication task to perform"},
                "context": {"type": "string", "description": "Additional context such as team contacts or project status", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_external_comms",
        "description": "Delegate to the External Communications Agent for client, vendor, and stakeholder emails via Gmail. Handles professional external correspondence.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The external communication task to perform"},
                "context": {"type": "string", "description": "Additional context such as stakeholder details or prior correspondence", "default": ""},
            },
            "required": ["task"],
        },
    },
    {
        "name": "delegate_to_prioritization",
        "description": "Delegate to the Prioritization Agent for ranking work items, building prioritization frameworks (weighted scoring, MoSCoW, RICE, Eisenhower, value vs. effort), and determining optimal execution order based on project complexity, business value, and customer priorities. This agent can execute Python code and generate Word documents and Excel spreadsheets.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The prioritization task to perform"},
                "context": {"type": "string", "description": "Additional context such as project list, complexity ratings, value assessments, or customer priorities", "default": ""},
            },
            "required": ["task"],
        },
    },
]
