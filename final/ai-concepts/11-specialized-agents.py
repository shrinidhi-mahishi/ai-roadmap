"""
Specialized Agents: coding, browser, data analysis, and research agents
are NOT different models -- they are different runtimes. Each specialty is
defined by its runtime (sandbox), oracle (what 'done' means), and identity.
"""

from __future__ import annotations
import subprocess
import json
import random
from dataclasses import dataclass
from typing import Any


# ═══ Section 1: Coding Agent (Generate -> Execute -> Check -> Iterate) ═══

def coding_agent(task: str, max_iterations: int = 3) -> dict:
    """Minimal coding agent: generate-execute-validate loop.
    The subprocess IS the sandbox (production uses Docker/Seatbelt/bwrap).
    Oracle: exit code 0 + hidden tests (SWE-bench FAIL_TO_PASS + PASS_TO_PASS).
    Identity: developer with workspace-scoped write access."""

    # Simulated LLM outputs -- attempt 1 has a bug, attempt 2 fixes it
    code_attempts = [
        "def greet(name):\n    return f'Hello, {namee}!'\nprint(greet('World'))",
        "def greet(name):\n    return f'Hello, {name}!'\nprint(greet('World'))",
    ]
    for i in range(min(max_iterations, len(code_attempts))):
        code = code_attempts[i]
        print(f"  Attempt {i + 1}: {code[:50]}...")
        try:
            result = subprocess.run(["python3", "-c", code],
                                    capture_output=True, text=True, timeout=5)
        except subprocess.TimeoutExpired:
            print(f"  -> TIMEOUT"); continue

        if result.returncode == 0:
            return {"status": "success", "output": result.stdout.strip(), "attempts": i + 1}
        # Error feedback is what makes this an AGENT, not a single completion
        print(f"  -> ERROR: {result.stderr.strip().split(chr(10))[-1]} (feeding back)")

    return {"status": "max_iterations", "attempts": max_iterations}


def demo_coding_agent():
    result = coding_agent("Write a greet function")
    print(f"  Result: {result['status']} after {result['attempts']} attempt(s)")
    if result.get("output"):
        print(f"  Output: {result['output']}")


# ═══ Section 2: Browser Agent (Navigate -> Extract -> Answer) ═══

@dataclass
class MockPage:
    """Simulates a browser page. Two observation channels:
    - Pixels (screenshots): works on any GUI, high token cost (~8k/step)
    - Structured (a11y tree): cheaper, refs survive reflow, no vision needed"""
    url: str
    title: str
    body_text: str


def browser_agent(url: str, question: str) -> dict:
    """Browser agent: navigate -> extract -> answer.
    Oracle: functional assertion on page/DB state (WebArena-style), NOT action trace.
    Identity: low-privilege site account, never admin SSO cookie."""

    # Simulated page load (real: Playwright or Anthropic browser_toolset)
    page = MockPage(url=url, title="Python - Wikipedia",
                    body_text="Python was created by Guido van Rossum and first "
                              "released in 1991. It emphasizes code readability.")
    print(f"  Navigated to: {page.title}")

    # Truncate content to fit context window, then feed to LLM
    content = page.body_text[:4000]
    if "created" in question.lower() or "who" in question.lower():
        answer = ("Python was created by Guido van Rossum in 1991. "
                  f"[Source: {page.title}]")
    else:
        answer = f"Based on {page.title}: {content[:100]}..."

    return {"answer": answer, "source_url": url, "page_title": page.title}


def demo_browser_agent():
    result = browser_agent("https://en.wikipedia.org/wiki/Python", "Who created Python?")
    print(f"  Answer: {result['answer']}")


# ═══ Section 3: Data Analysis Agent (Contract -> SQL -> Execute -> Interpret) ═══

@dataclass
class MockWarehouse:
    """Simulates a warehouse with dual credentials (Databricks Genie model):
    compute identity != data identity. RLS on tables, not in the prompt."""
    tables: dict[str, list[dict]]

    def execute(self, sql: str) -> dict:
        if "DROP" in sql.upper() or "DELETE" in sql.upper():
            return {"error": "Write operations blocked -- read-only role"}
        return {"rows": self.tables.get("sales", [])[:5], "row_count": 5}


def data_agent(question: str, warehouse: MockWarehouse) -> dict:
    """Data agent: question -> analysis contract -> SQL -> validate -> interpret.
    The contract pattern prevents 'syntactically valid, semantically wrong' queries.
    Oracle: execution accuracy + RLS compliance. Identity: end-user warehouse role."""

    contract = {"metric": "Monthly revenue by region", "grain": "region x month",
                "check": "total > 0, no null regions"}
    print(f"  Contract: {contract['metric']}")

    sql = "SELECT region, SUM(amount) as revenue FROM sales GROUP BY region LIMIT 100"
    print(f"  SQL: {sql}")

    result = warehouse.execute(sql)
    if "error" in result:
        return {"status": "blocked", "reason": result["error"]}

    rows = result.get("rows", [])
    total = sum(r.get("amount", 0) for r in rows)
    return {"status": "success", "sql": sql, "row_count": len(rows),
            "interpretation": f"Total revenue: ${total:,.0f} across {len(rows)} regions"}


def demo_data_agent():
    warehouse = MockWarehouse(tables={"sales": [
        {"region": "North", "amount": 50000}, {"region": "South", "amount": 35000},
        {"region": "East", "amount": 42000}, {"region": "West", "amount": 61000},
    ]})
    result = data_agent("Revenue by region?", warehouse)
    print(f"  {result.get('interpretation', result.get('reason', ''))}")


# ═══ Section 4: Research Agent (Search -> Collect -> Synthesize -> Cite) ═══

def mock_search(query: str) -> list[dict]:
    return [{"title": f"Source: {query}", "url": f"https://example.com/{i}",
             "snippet": f"Finding {i} about {query[:30]}."} for i in range(1, 4)]


def research_agent(question: str, max_searches: int = 3) -> dict:
    """Research agent: plan -> search -> synthesize -> cite.
    Unlike coding (tight edit-test loop), research is breadth-first compression.
    Citation as a separate pass (CitationAgent) beats 'cite as you write'.
    Oracle: rubric (factuality, citation accuracy, completeness)."""

    sources, queries = [], []
    for variant in ["overview", "recent 2025", "challenges"]:
        query = f"{question} {variant}"
        sources.extend(mock_search(query))
        queries.append(query)
        if len(queries) >= max_searches:
            break

    synthesis = f"Based on {len(sources)} sources:\n"
    for i, src in enumerate(sources[:3], 1):
        synthesis += f"  [{i}] {src['snippet']} ({src['url']})\n"

    return {"answer": synthesis, "sources": sources,
            "searches_used": len(queries), "citation_accuracy": 1.0}


def demo_research_agent():
    result = research_agent("Key patterns in multi-agent systems")
    print(f"  Searches: {result['searches_used']}, Sources: {len(result['sources'])}")
    print(f"  {result['answer'][:200]}")


# ═══ Section 5: Agent Specialization Selector (Router) ═══

SPECIALIZATIONS = {
    "coding":   {"kw": ["code", "bug", "test", "function", "refactor"],
                 "runtime": "sandboxed bash + editor + tests + git",
                 "oracle": "hidden tests (FAIL_TO_PASS + PASS_TO_PASS)"},
    "browser":  {"kw": ["website", "page", "click", "navigate", "extract"],
                 "runtime": "a11y-tree or screenshots in isolated browser",
                 "oracle": "page/DB state assertion (WebArena-style)"},
    "research": {"kw": ["research", "find", "compare", "report", "trends"],
                 "runtime": "web search + fetch + MCP search/fetch",
                 "oracle": "rubric (factuality, citations, completeness)"},
    "data":     {"kw": ["sql", "query", "database", "revenue", "metrics"],
                 "runtime": "read-only SQL + notebooks + warehouse APIs",
                 "oracle": "execution accuracy + trusted-asset match"},
}


def select_specialization(task: str) -> dict:
    """Route a task to the right specialized agent.
    Coding is a POOR fit for orchestrator-worker research pattern
    (few parallelizable subtasks). Do not force a research DAG onto a git loop."""
    task_lower = task.lower()
    scores = {s: sum(1 for kw in cfg["kw"] if kw in task_lower)
              for s, cfg in SPECIALIZATIONS.items()}
    best = max(scores, key=scores.get)
    return {"selected": best, "runtime": SPECIALIZATIONS[best]["runtime"],
            "oracle": SPECIALIZATIONS[best]["oracle"], "scores": scores}


def demo_selector():
    tasks = [
        "Fix the login bug in auth.py and add a test",
        "Navigate to the pricing page and extract plan details",
        "Research recent developments in quantum computing",
        "Query the database for monthly revenue by region",
    ]
    for task in tasks:
        r = select_specialization(task)
        print(f"  '{task[:45]}...' -> {r['selected'].upper()} {r['scores']}")
        print(f"    Runtime: {r['runtime']}")


# ═══ Main ═══

if __name__ == "__main__":
    print("=" * 60)
    print("SPECIALIZED AGENTS -- Interview Prep Demos")
    print("=" * 60)

    print("\n--- 1. Coding Agent (Generate -> Execute -> Validate) ---")
    demo_coding_agent()

    print("\n--- 2. Browser Agent (Navigate -> Extract -> Answer) ---")
    demo_browser_agent()

    print("\n--- 3. Data Analysis Agent (Contract -> SQL -> Validate) ---")
    demo_data_agent()

    print("\n--- 4. Research Agent (Search -> Synthesize -> Cite) ---")
    demo_research_agent()

    print("\n--- 5. Agent Specialization Selector ---")
    demo_selector()

    print("\n" + "=" * 60)
    print("Key takeaways:")
    print("  - Specialty = runtime + oracle + identity (not model weights)")
    print("  - Coding: tight edit-test loop; Research: breadth-first compression")
    print("  - Data: RLS on tables, not in prompt; dual credentials")
    print("  - Browser: judge by end-state, not action trace")
    print("  - Citation as a separate pass beats 'cite as you write'")
