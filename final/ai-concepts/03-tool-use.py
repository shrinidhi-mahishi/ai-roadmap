"""
Tool Use: JSON Schema Definitions, Function Calling Loops, and Tool Registries.

Tool use (function calling) turns an LLM from a text generator into an agent. The
model NEVER executes tools -- it emits structured JSON, your code runs them, and
you feed results back. This universal loop works across all providers.
"""

import json
import time
import random
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor, as_completed


# ======================================================================
# 1. JSON Schema Tool Definition
# ======================================================================

# Exact format Anthropic/OpenAI expect. In strict mode, ALL properties
# must be in "required" and additionalProperties must be false.
TOOL_DEFINITIONS = [
    {
        "name": "get_weather",
        "description": "Get current weather for a city.",
        "input_schema": {
            "type": "object",
            "properties": {
                "city":  {"type": "string", "description": "City name"},
                "units": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["city"],
            "additionalProperties": False,
        },
    },
    {
        "name": "search_products",
        "description": "Query the product database by keyword.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query":       {"type": "string", "description": "Search term"},
                "max_results": {"type": "integer", "default": 5},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    },
]


# ======================================================================
# 2. Function Calling Loop (Mock LLM)
# ======================================================================

# Simulated tool implementations
def get_weather(city, units="celsius"):
    temps = {"San Francisco": 18, "Tokyo": 28, "London": 14, "NYC": 22}
    t = temps.get(city, 20)
    if units == "fahrenheit":
        t = int(t * 9 / 5 + 32)
    return {"city": city, "temperature": t, "units": units, "condition": "cloudy"}


def search_products(query, max_results=5):
    products = [{"name": "Widget A", "price": 29.99}, {"name": "Widget B", "price": 49.99}]
    return {"results": [p for p in products if query.lower() in p["name"].lower()][:max_results]}


TOOL_IMPLS = {"get_weather": get_weather, "search_products": search_products}


def mock_llm(messages):
    """Simulate LLM deciding to call a tool or give a final answer."""
    last = messages[-1]
    if last.get("role") == "tool_result":
        return {"stop": "end_turn", "content": [{"type": "text", "text": f"Result: {last['content']}"}]}
    if "weather" in last.get("content", "").lower():
        return {"stop": "tool_use", "content": [{
            "type": "tool_use", "id": f"call_{random.randint(1000,9999)}",
            "name": "get_weather", "input": {"city": "Tokyo", "units": "celsius"},
        }]}
    return {"stop": "end_turn", "content": [{"type": "text", "text": "How can I help?"}]}


def tool_calling_loop(query, max_turns=5):
    """The universal loop: send -> parse -> execute -> feed back -> repeat.

    Critical invariants:
    - Return ALL tool results in ONE user message (match IDs 1:1)
    - Include is_error: true for failures (never drop IDs)
    - Loop until stop_reason != "tool_use"
    """
    messages = [{"role": "user", "content": query}]
    for turn in range(max_turns):
        resp = mock_llm(messages)
        print(f"  Turn {turn+1}: stop={resp['stop']}")
        if resp["stop"] != "tool_use":
            answer = resp["content"][0]["text"]
            print(f"  Answer: {answer}")
            return answer
        for block in resp["content"]:
            if block["type"] == "tool_use":
                func = TOOL_IMPLS.get(block["name"])
                print(f"    -> {block['name']}({block['input']})")
                if func:
                    result = func(**block["input"])
                    messages.append({"role": "tool_result", "tool_use_id": block["id"],
                                     "content": json.dumps(result)})
                else:
                    messages.append({"role": "tool_result", "tool_use_id": block["id"],
                                     "is_error": True, "content": f"Unknown: {block['name']}"})
    return None


# ======================================================================
# 3. Tool Registry Pattern
# ======================================================================

@dataclass
class ToolRegistry:
    """Register, look up, and execute tools by name.

    Production: centralized gateway with RBAC, audit logging, and
    dynamic discovery. At 500+ tools, use deferred loading to cut
    tokens by 14x (1.15M -> 83K tokens).
    """
    _tools: dict = field(default_factory=dict)

    def register(self, name, func, description=""):
        self._tools[name] = {"func": func, "desc": description}

    def list_tools(self):
        return [{"name": n, "description": t["desc"]} for n, t in self._tools.items()]

    def execute(self, name, args):
        if name not in self._tools:
            return {"is_error": True, "content": f"Tool '{name}' not found"}
        try:
            return {"content": json.dumps(self._tools[name]["func"](**args))}
        except Exception as e:
            return {"is_error": True, "content": f"Error: {e}"}


# ======================================================================
# 4. Parallel Tool Calling
# ======================================================================

def execute_parallel(tool_calls, registry, max_workers=4, timeout=10):
    """Execute multiple tool calls concurrently.

    Cuts latency to the slowest call (not sum). W&D study: 3.7x speedup,
    6.7x cost reduction. Failure mode: tools with shared state create race
    conditions -- force sequential when tools target the same resource.
    """
    results = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(registry.execute, c["name"], c["args"]): c["id"]
                   for c in tool_calls}
        for f in as_completed(futures):
            call_id = futures[f]
            try:
                results[call_id] = f.result(timeout=timeout)
            except Exception as e:
                results[call_id] = {"is_error": True, "content": str(e)}
    return results


# ======================================================================
# 5. Error Handling in Tool Execution
# ======================================================================

def execute_with_retry(func, args, max_retries=3, retry_on=(TimeoutError,)):
    """Retry with exponential backoff + full jitter.

    Only retry safe failures (timeout, 429, 5xx). NEVER retry POST
    without an idempotency key -- you risk double-charging.
    """
    for attempt in range(max_retries):
        try:
            return {"success": True, "result": func(**args), "attempts": attempt + 1}
        except retry_on as e:
            if attempt == max_retries - 1:
                return {"success": False, "error": str(e), "attempts": attempt + 1}
            delay = min(1.0 * (2 ** attempt), 30.0)
            jitter = random.uniform(0, delay)
            print(f"    Retry {attempt+1}/{max_retries} after {jitter:.2f}s: {e}")
            time.sleep(jitter * 0.01)  # Shortened for demo
        except Exception as e:
            return {"success": False, "error": str(e), "attempts": attempt + 1}


def flaky_api(city):
    """Simulate an API that fails 60% of the time."""
    if random.random() < 0.6:
        raise TimeoutError(f"Timeout for {city}")
    return {"city": city, "temp": 22}


# ======================================================================
# Main: Demo All Snippets
# ======================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("1. JSON SCHEMA TOOL DEFINITIONS")
    print("=" * 60)
    for t in TOOL_DEFINITIONS:
        props = ", ".join(f"{k}: {v['type']}" for k, v in t["input_schema"]["properties"].items())
        req = t["input_schema"].get("required", [])
        print(f"  {t['name']}({props})  required={req}")
    print()

    print("=" * 60)
    print("2. FUNCTION CALLING LOOP")
    print("=" * 60)
    tool_calling_loop("What's the weather in Tokyo?")
    print()

    print("=" * 60)
    print("3. TOOL REGISTRY PATTERN")
    print("=" * 60)
    reg = ToolRegistry()
    reg.register("get_weather", get_weather, "Get weather for a city")
    reg.register("search_products", search_products, "Search product DB")
    print(f"  Registered: {[t['name'] for t in reg.list_tools()]}")
    print(f"  get_weather('London'): {reg.execute('get_weather', {'city': 'London'})}")
    print(f"  unknown_tool():       {reg.execute('nope', {})}")
    print()

    print("=" * 60)
    print("4. PARALLEL TOOL CALLING")
    print("=" * 60)
    calls = [
        {"id": "c1", "name": "get_weather", "args": {"city": "Tokyo"}},
        {"id": "c2", "name": "get_weather", "args": {"city": "London"}},
        {"id": "c3", "name": "search_products", "args": {"query": "Widget"}},
    ]
    print(f"  Executing {len(calls)} calls in parallel...")
    for cid, res in execute_parallel(calls, reg).items():
        print(f"    {cid}: {res}")
    print()

    print("=" * 60)
    print("5. ERROR HANDLING WITH RETRY")
    print("=" * 60)
    random.seed(42)
    result = execute_with_retry(flaky_api, {"city": "NYC"}, max_retries=5)
    print(f"  success={result['success']}, attempts={result['attempts']}")
    print("  Rules: retry 408/429/5xx only. Never POST without idempotency key.")
