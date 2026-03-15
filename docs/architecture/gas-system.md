# Gas System

## Overview

The gas system gives operators and users explicit control over how many LLM tokens an agent is permitted to consume on a task or session. It is the primary mechanism for controlling cost and preventing runaway agent loops.

Input tokens and output tokens are tracked and budgeted **separately**. Every job and session has a `gas_limit_input` and a `gas_limit_output`, and corresponding `gas_used_input` and `gas_used_output` counters. The agent enters `out_of_gas` state when either counter reaches its limit. This lets operators tune budgets for prompt-heavy vs generation-heavy workloads independently, and reflects that input and output tokens have different per-token costs on most LLM providers.

When `out_of_gas` is reached, all context is preserved, no further LLM calls are made, and the run is fully resumable once additional gas is allocated to whichever limit was hit.

## Default Gas Limits

Default gas limits are configured at the gateway level via environment variables and can be overridden per project in `.agents/config.yaml` and per session in the session launcher:

| Level | Config keys | Default |
|---|---|---|
| System default (jobs) | `DEFAULT_JOB_INPUT_GAS_LIMIT` / `DEFAULT_JOB_OUTPUT_GAS_LIMIT` | `80,000` / `20,000` tokens |
| System default (sessions) | `DEFAULT_SESSION_INPUT_GAS_LIMIT` / `DEFAULT_SESSION_OUTPUT_GAS_LIMIT` | `160,000` / `40,000` tokens |
| Project override (jobs) | `gas_limit_input` / `gas_limit_output` in `.agents/config.yaml` | inherits system default |
| Session override | `gas_limit_input` / `gas_limit_output` in `SessionContext` | inherits system default |

## Gas Flow

```
Agent makes LLM call
        ↓
LLM response received — input_tokens and output_tokens extracted separately
        ↓
Agent emits gas_updated event:
  {gas_used_input, gas_limit_input, gas_used_output, gas_limit_output,
   input_tokens, output_tokens}
        ↓
AgentLogger forwards to gateway → gateway updates both counters in DB
        ↓
gas_used_input >= gas_limit_input  OR  gas_used_output >= gas_limit_output?
    NO  → continue agent loop normally
    YES → Agent emits out_of_gas event (includes which limit was hit)
          Agent suspends loop (does not make another LLM call)
          Gateway sets job/session status → out_of_gas
          Dashboard shows both gas meters, prompts user to top up
        ↓
User reviews run in dashboard, clicks "Add gas", enters top-up amounts
        ↓
POST /agents/{id}/gas or POST /sessions/{id}/gas
  body: {"input_amount": N, "output_amount": M}  (either field optional)
        ↓
Gateway increments the specified limit(s) in DB
Gateway calls agent.add_gas(input_amount, output_amount) via
  POST /internal/jobs/{id}/add-gas
        ↓
Agent increments limit(s), re-enters loop from where it paused
Status → running, agent continues
```

## Gas in the Agent Class

The `Agent` class tracks input and output gas independently:

- `self._gas_used_input` — accumulated input token count across all LLM calls
- `self._gas_used_output` — accumulated output token count across all LLM calls
- `self._gas_limit_input` — input token budget; increases when `add_gas()` is called
- `self._gas_limit_output` — output token budget; increases when `add_gas()` is called
- After each LLM call: `self._gas_used_input += input_tokens; self._gas_used_output += output_tokens`
- Before each new LLM call: if either limit is exceeded, emit `out_of_gas` and `await self._gas_event.wait()` — an `asyncio.Event` set by `add_gas()`
- `add_gas(input_amount=0, output_amount=0)` increments the specified limit(s) and calls `self._gas_event.set()`, which unblocks the suspended loop

## Gas API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/agents/{id}/gas` | Add gas to a job; body: `{"input_amount": N, "output_amount": M}` |
| `POST` | `/sessions/{id}/gas` | Add gas to a session; body: `{"input_amount": N, "output_amount": M}` |
| `GET` | `/agents/{id}/gas` | Return `gas_used_input`, `gas_used_output`, `gas_limit_input`, `gas_limit_output`, `topup_history` |
| `GET` | `/sessions/{id}/gas` | Return `gas_used_input`, `gas_used_output`, `gas_limit_input`, `gas_limit_output`, `topup_history` |

Both `input_amount` and `output_amount` are optional in the POST body — supply only the limit(s) that need topping up. The `POST` endpoints are accessible to any authenticated user.

## Gas in the Dashboard

Every job card and session card in the dashboard displays two **gas meters** — one for input tokens showing `gas_used_input / gas_limit_input`, one for output tokens showing `gas_used_output / gas_limit_output`. Both meters update live via the SSE stream as `gas_updated` events arrive.

When a job or session reaches `out_of_gas` status:
- The exhausted meter fills to 100% and turns amber; the other meter shows its current fill
- A banner appears: *"Agent paused — out of gas. Review the execution trace below and add more tokens to continue."*
- Two numeric inputs appear — one for input tokens and one for output tokens — each pre-populated with the system default top-up amount, plus an **Add Gas** button
- Submitting calls `POST /agents/{id}/gas` or `POST /sessions/{id}/gas`; the status transitions back to `running` and the meters reset to the new ratios

The full execution trace remains visible while the run is `out_of_gas`, so the user has full context before deciding how much gas to add and which type.

---
