"""
app/fastpath – mockup of a two-speed runtime (served under /api/v2).

1. One fast LLM call ("select") sees the fact-store structure (concepts + fact sheet)
   and returns the task's variables with values and fact IDs directly. Validated
   variables are returned to the client immediately.
2. Every variable the store cannot answer is resolved by its own "derive" call over the
   raw passages, all in parallel and in the background. The client picks the results up
   with GET /api/v2/screens/{id} (polling) or GET /api/v2/screens/{id}/stream (SSE).
"""
