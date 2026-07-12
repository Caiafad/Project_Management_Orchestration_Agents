"""
Project Management MCP Server

A Python Standard MCP Server that exposes 7 specialized project management
sub-agents as tools. The orchestrator agent connects to this server to
delegate tasks to the appropriate specialist.
"""

import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "project-management-agent",
    instructions=(
        "Project Management Agent Server. Provides 8 specialized sub-agents for "
        "project planning, scope definition, project orchestration, business management, "
        "financial management, internal communications, external communications, and prioritization. "
        "Each tool delegates a task to the corresponding specialist agent."
    ),
)


@mcp.tool()
def delegate_to_project_planning(task: str, context: str = "") -> str:
    """
    Delegate a task to the Project Planning Agent.

    This agent breaks down projects into work breakdown structures, creates Gantt chart data,
    identifies dependencies, estimates costs, and develops the ideal project path.
    It can execute Python code for analysis and search the knowledge base for best practices.
    It can also generate Word documents for formal project plans.

    Args:
        task: Description of the project planning task to perform.
        context: Additional context such as project details, constraints, or prior analysis.
    """
    from agents.project_planning import ProjectPlanningAgent
    agent = ProjectPlanningAgent()
    return agent.run(task, context)


@mcp.tool()
def delegate_to_scope_definition(task: str, context: str = "") -> str:
    """
    Delegate a task to the Scope Definition Agent.

    This agent defines what is in scope and out of scope for projects, creates Business
    Requirements Documents (BRDs), and develops scope definition documents.
    It can generate Word documents for formal scope and requirements deliverables.

    Args:
        task: Description of the scope definition task to perform.
        context: Additional context such as project goals, stakeholder needs, or constraints.
    """
    from agents.scope_definition import ScopeDefinitionAgent
    agent = ScopeDefinitionAgent()
    return agent.run(task, context)


@mcp.tool()
def delegate_to_project_orchestration(task: str, context: str = "") -> str:
    """
    Delegate a task to the Project Orchestration Agent.

    This agent assigns team members to roles, tasks, and timelines. It drafts internal
    project documentation including RACI matrices, role descriptions, and accountability structures.
    It can generate Word documents for internal project documentation.

    Args:
        task: Description of the orchestration task to perform.
        context: Additional context such as team member info, skills, availability, or project plan.
    """
    from agents.project_orchestration import ProjectOrchestrationAgent
    agent = ProjectOrchestrationAgent()
    return agent.run(task, context)


@mcp.tool()
def delegate_to_business_manager(task: str, context: str = "") -> str:
    """
    Delegate a task to the Business Manager Agent.

    This agent creates executive presentations, project roadmaps, and leadership briefings.
    It translates technical project details into business-friendly formats.
    It can generate both PowerPoint presentations and Word documents.

    Args:
        task: Description of the business/executive communication task to perform.
        context: Additional context such as project scope, goals, milestones, or audience info.
    """
    from agents.business_manager import BusinessManagerAgent
    agent = BusinessManagerAgent()
    return agent.run(task, context)


@mcp.tool()
def delegate_to_financial_manager(task: str, context: str = "") -> str:
    """
    Delegate a task to the Financial Manager Agent.

    This agent calculates project costs, develops budget breakdowns, performs ROI analysis,
    and creates financial documentation. It can execute Python code for calculations
    and search the knowledge base for benchmarks and rates.
    It can also generate Word documents for financial reports.

    Args:
        task: Description of the financial analysis task to perform.
        context: Additional context such as team size, rates, project timeline, or budget constraints.
    """
    from agents.financial_manager import FinancialManagerAgent
    agent = FinancialManagerAgent()
    return agent.run(task, context)


@mcp.tool()
def delegate_to_internal_comms(task: str, context: str = "") -> str:
    """
    Delegate a task to the Internal Communications Agent.

    This agent handles internal team communications via Gmail and Slack. It serves as a
    scrum master, facilitating standups, sprint communications, and dependency coordination
    between team members.

    Args:
        task: Description of the internal communication task to perform.
        context: Additional context such as team member contacts, project status, or blockers.
    """
    from agents.internal_comms import InternalCommsAgent
    agent = InternalCommsAgent()
    return agent.run(task, context)


@mcp.tool()
def delegate_to_external_comms(task: str, context: str = "") -> str:
    """
    Delegate a task to the External Communications Agent.

    This agent handles external stakeholder communications via Gmail. It drafts professional
    emails for clients, vendors, and partners. It works with the Business Manager's outputs
    for external-facing content.

    Args:
        task: Description of the external communication task to perform.
        context: Additional context such as stakeholder info, project updates, or prior correspondence.
    """
    from agents.external_comms import ExternalCommsAgent
    agent = ExternalCommsAgent()
    return agent.run(task, context)


@mcp.tool()
def delegate_to_prioritization(task: str, context: str = "") -> str:
    """
    Delegate a task to the Prioritization Agent.

    This agent evaluates projects and tasks based on complexity, business value, and
    customer-defined priorities. It builds prioritization frameworks (weighted scoring,
    MoSCoW, RICE, Eisenhower, value vs. effort) and maps optimal execution order.
    It can execute Python code and generate Word documents and Excel spreadsheets.

    Args:
        task: Description of the prioritization task to perform.
        context: Additional context such as project list, complexity ratings, value assessments, or priorities.
    """
    from agents.prioritization import PrioritizationAgent
    agent = PrioritizationAgent()
    return agent.run(task, context)


if __name__ == "__main__":
    mcp.run()
