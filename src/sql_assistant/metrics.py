"""Centralised Prometheus metric definitions.

All counters, histograms and gauges are defined here so every module
imports from a single source of truth.  The `/metrics` endpoint is
exposed via `api.py`.
"""

from prometheus_client import Counter, Histogram

# ---------------------------------------------------------------------------
# Tool-level metrics
# ---------------------------------------------------------------------------

TOOL_CALLS_TOTAL = Counter(
    "sql_assistant_tool_calls_total",
    "Total number of tool invocations",
    ["tool_name", "status"],  # status: success | error
)

TOOL_DURATION_SECONDS = Histogram(
    "sql_assistant_tool_duration_seconds",
    "Time spent inside a tool call",
    ["tool_name"],
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

# ---------------------------------------------------------------------------
# SQL validation metrics
# ---------------------------------------------------------------------------

VALIDATION_RESULTS_TOTAL = Counter(
    "sql_assistant_validation_results_total",
    "Outcome of SQL validation checks",
    ["result"],  # result: valid | invalid
)

# ---------------------------------------------------------------------------
# Database query metrics
# ---------------------------------------------------------------------------

QUERY_EXECUTION_DURATION_SECONDS = Histogram(
    "sql_assistant_query_execution_duration_seconds",
    "Wall-clock time to execute a SQL query against PostgreSQL",
    ["source"],  # source: agent_tool | local_api
    buckets=(0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
)

QUERY_ROWS_RETURNED = Histogram(
    "sql_assistant_query_rows_returned",
    "Number of rows returned by executed queries",
    buckets=(0, 1, 5, 10, 25, 50, 100, 250, 500, 1000),
)

# ---------------------------------------------------------------------------
# LLM metrics
# ---------------------------------------------------------------------------

LLM_CALLS_TOTAL = Counter(
    "sql_assistant_llm_calls_total",
    "Number of LLM invocations",
    ["node"],  # node: agent
)

# ---------------------------------------------------------------------------
# HTTP / API metrics
# ---------------------------------------------------------------------------

REQUESTS_TOTAL = Counter(
    "sql_assistant_requests_total",
    "Total HTTP requests received",
    ["endpoint", "method", "status_code"],
)

REQUEST_DURATION_SECONDS = Histogram(
    "sql_assistant_request_duration_seconds",
    "HTTP request latency",
    ["endpoint", "method"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)

# ---------------------------------------------------------------------------
# Human-in-the-loop metrics
# ---------------------------------------------------------------------------

APPROVAL_DECISIONS_TOTAL = Counter(
    "sql_assistant_approval_decisions_total",
    "Human approval / rejection decisions",
    ["decision"],  # decision: approve | reject
)
