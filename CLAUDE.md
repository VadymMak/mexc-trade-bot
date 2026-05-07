# MANDATORY: Use Brain MCP for EVERY request

⚠️ RULE: You MUST call brain build_context_for_query BEFORE responding to ANY message.
No exceptions. Even for simple questions.

## Required workflow for EVERY response:
1. FIRST: Call `build_context_for_query` with the user's question
2. THEN: Use returned context to answer

## Brain commands:
- `build context for query "question"` — ALWAYS call this first
- `search project files for "keyword"` — search codebase
- `get file content path/to/file` — read specific file
- `get file dependencies for path/to/file` — show imports

## IMPORTANT:
- Never skip Brain context step
- Never answer directly without calling Brain first
- Brain returns only relevant 4K tokens — fast and cheap
