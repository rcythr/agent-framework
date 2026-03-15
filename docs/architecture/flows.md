# Flows

## End-to-End Flow

```
1. GitLab event fires (MR opened, comment posted, push, or manual CI job)
        ↓
2. Gateway receives webhook, validates secret token
        ↓
3. Gateway maps event → TaskSpec, calls config loader:
   a. Fetches {AGENT_CONFIG_DIR}/config.yaml (default .agents/config.yaml) from project repo at event commit SHA via GitLab API
   b. Merges with global defaults → resolves AgentConfig (skills, tools, prompt, image)
   c. If project has a custom Dockerfile: checks image cache by Dockerfile blob SHA;
      builds derived image via Kaniko Job if not cached; waits for push to registry
        ↓
4. Gateway creates JobRecord in DB (status: pending), spawns ephemeral K8s Job
   using resolved AgentConfig image tag and composed system prompt
        ↓
5. Worker pod boots, AgentLogger initialised with gateway callback URL + job ID
        ↓
6. Worker POSTs status update → gateway sets job to running in DB
        ↓
7. Agent calls LLM:
   AgentLogger emits llm_query event → gateway persists + fans out to SSE subscribers
        ↓
8. LLM responds with tool call decision:
   AgentLogger emits llm_response event → gateway persists + fans out
        ↓
9. Agent executes tool (e.g. get_mr_diff, post_mr_comment, commit_file):
   AgentLogger emits tool_call event → gateway persists + fans out
   Tool runs against GitLab API
   AgentLogger emits tool_result event → gateway persists + fans out
        ↓
10. Steps 7–9 repeat for each agent loop iteration
        ↓
    After each LLM call: Agent emits gas_updated event → gateway updates gas_used_input/gas_used_output in DB
    If gas_used_input >= gas_limit_input or gas_used_output >= gas_limit_output: Agent emits out_of_gas → gateway sets status out_of_gas
    Dashboard shows full trace + top-up prompt; user can add gas to resume
        ↓
11. Agent loop completes:
    AgentLogger emits complete event → worker POSTs status: completed to gateway
        ↓
12. Pod exits cleanly, Kubernetes TTL cleans up Job after 5 minutes
        ↓
13. Dashboard reflects final state; log panel shows full execution trace for replay
```

---

## Interactive Session Flow

This supplements the webhook-triggered end-to-end flow above, describing the lifecycle of a user-initiated interactive session.

```
1. User opens "New Session" in the dashboard, selects project + branch + goal
        ↓
2. Dashboard calls GET /projects/search, /branches, /mrs to populate launcher
        ↓
3. User clicks Launch → POST /sessions with SessionContext
        ↓
4. Gateway resolves AgentConfig (config loader: fetch .agents/config.yaml,
   merge skills/tools with session overrides, compose prompt, resolve image)
        ↓
5. Gateway creates SessionRecord in DB (status: configuring),
   spawns K8s Job with SESSION_ID + GATEWAY_URL env vars
        ↓
6. Worker pod boots in session mode, connects to gateway,
   updates session status → running
        ↓
7. Agent loop begins. At the top of each iteration:
   Worker calls POST /internal/sessions/{id}/interrupt-check
   → if an interrupt is pending, it is injected into LLM context
        ↓
8. Agent calls LLM with goal + conversation history:
   AgentLogger emits llm_query → gateway persists, SSE fans out to dashboard
        ↓
9. LLM responds — either:
   a) Tool call decision → tool executes, result logged, loop continues (→ step 7)
   b) Natural language response → emitted as agent_response SessionMessage,
      appears in dashboard conversation thread
   c) Input request → agent calls POST /internal/sessions/{id}/await-input
      with question; gateway transitions session → waiting_for_user;
      question appears in conversation thread; worker blocks
        ↓
10. (If waiting_for_user) User types answer → POST /sessions/{id}/messages
    Gateway enqueues message, transitions session → running,
    await-input call returns with user's answer → agent loop resumes (→ step 7)
        ↓
11. (If running) User sends an interrupt → POST /sessions/{id}/messages
    Gateway enqueues interrupt; picked up at next iteration (→ step 7)
        ↓
12. Agent determines goal is complete, emits complete event
    Worker POSTs status: complete to gateway
        ↓
13. Session status → complete, conversation input disabled,
    full execution trace available for replay
```

---
