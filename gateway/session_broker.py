import asyncio
from collections import defaultdict


class SessionBroker:
    """
    In-memory message queue and state management for active sessions.
    The DB is the source of truth; this holds only live in-flight data.
    """

    def __init__(self):
        # Per-session queue for user input responses (await_user_input blocks on this)
        self._input_queues: dict[str, asyncio.Queue] = {}
        # Per-session pending interrupt message
        self._interrupts: dict[str, str] = {}
        # Per-session status (for transition tracking)
        self._statuses: dict[str, str] = {}

    async def register(self, session_id: str) -> None:
        """Called when a new session worker connects."""
        self._input_queues[session_id] = asyncio.Queue()
        self._statuses[session_id] = "running"

    async def send_to_agent(
        self, session_id: str, message: str, message_type: str
    ) -> None:
        """
        Enqueue a user message; transition waiting_for_user → running if applicable.
        For interrupts: set the interrupt flag instead of enqueuing.
        """
        if message_type == "interrupt":
            self._interrupts[session_id] = message
        else:
            # Ensure queue exists
            if session_id not in self._input_queues:
                self._input_queues[session_id] = asyncio.Queue()
            await self._input_queues[session_id].put(message)
            if self._statuses.get(session_id) == "waiting_for_user":
                self._statuses[session_id] = "running"

    async def await_user_input(self, session_id: str, question: str) -> str:
        """
        Called by the worker when agent emits input_request.
        Transitions session to waiting_for_user.
        Blocks until send_to_agent is called.
        Returns the user's response string.
        """
        if session_id not in self._input_queues:
            self._input_queues[session_id] = asyncio.Queue()
        self._statuses[session_id] = "waiting_for_user"
        response = await self._input_queues[session_id].get()
        self._statuses[session_id] = "running"
        return response

    def check_interrupt(self, session_id: str) -> str | None:
        """
        Called by worker at start of each loop iteration.
        Returns pending interrupt message and clears it, or None.
        """
        return self._interrupts.pop(session_id, None)

    async def cleanup(self, session_id: str) -> None:
        """Called on terminal state; removes queues."""
        self._input_queues.pop(session_id, None)
        self._interrupts.pop(session_id, None)
        self._statuses.pop(session_id, None)
