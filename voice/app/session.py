"""Voice session manager that orchestrates voice agent and Eigent connections."""

import asyncio
import logging
from typing import Optional, Callable
from dataclasses import dataclass, field

from app.deepgram_agent import VoiceAgent
from app.eigent_client import EigentClient
from app.models import SSEEvent, TaskStatus

logger = logging.getLogger(__name__)


@dataclass
class VoiceSession:
    """Manages a voice session including agent and Eigent connections.

    This is the central orchestrator that:
    - Manages both VoiceAgent (Deepgram) and EigentClient connections
    - Registers function handlers that the voice agent can call
    - Subscribes to SSE events from Eigent to trigger voice notifications
    - Handles callbacks for UI updates (transcripts, audio, etc.)
    """

    project_id: str
    auth_token: Optional[str] = None

    # Callbacks for UI updates
    on_user_speech: Optional[Callable[[str], None]] = None
    on_agent_speech: Optional[Callable[[str], None]] = None
    on_audio_out: Optional[Callable[[bytes], None]] = None
    on_task_submitted: Optional[Callable[[str], None]] = None
    on_status_update: Optional[Callable[[TaskStatus], None]] = None
    # Barge-in callbacks
    on_user_started_speaking: Optional[Callable[[], None]] = None
    on_agent_started_speaking: Optional[Callable[[], None]] = None

    # Internal state
    _agent: Optional[VoiceAgent] = field(default=None, init=False)
    _eigent: Optional[EigentClient] = field(default=None, init=False)
    _sse_task: Optional[asyncio.Task] = field(default=None, init=False)
    _active: bool = field(default=False, init=False)

    async def start(self):
        """Start the voice session.

        Initializes both Eigent client and Deepgram voice agent connections,
        registers function handlers, and starts SSE event subscription.

        Raises:
            Exception: If connection to either service fails.
        """
        logger.info("Starting voice session for project %s", self.project_id)

        try:
            # Initialize Eigent client
            self._eigent = EigentClient(self.auth_token)
            await self._eigent.__aenter__()
            logger.debug("Eigent client initialized")

            # Initialize voice agent with callbacks
            self._agent = VoiceAgent(
                on_transcript=self._handle_transcript,
                on_agent_response=self._handle_agent_response,
                on_audio=self._handle_audio,
                on_user_started_speaking=self._handle_user_started_speaking,
                on_agent_started_speaking=self._handle_agent_started_speaking,
            )

            # Register function handlers
            self._agent.register_function("submit_task", self._fn_submit_task)
            self._agent.register_function(
                "get_project_context", self._fn_get_project_context
            )
            self._agent.register_function("get_task_status", self._fn_get_task_status)
            self._agent.register_function("confirm_start", self._fn_confirm_start)
            self._agent.register_function("cancel_task", self._fn_cancel_task)
            logger.debug("Function handlers registered")

            # Connect to Deepgram
            await self._agent.connect()
            logger.debug("Voice agent connected")

            # SSE subscription disabled - backend uses embedded SSE in POST /chat response
            # TODO: Integrate with actual backend SSE architecture
            self._active = True
            # self._sse_task = asyncio.create_task(self._subscribe_events())
            logger.info("Voice session started successfully for project %s", self.project_id)

        except Exception as e:
            logger.error(
                "Failed to start voice session for project %s: %s",
                self.project_id,
                e,
                exc_info=True,
            )
            # Clean up any partial initialization
            await self._cleanup()
            raise

    async def stop(self):
        """Stop the voice session.

        Cancels SSE subscription and disconnects from both services.
        """
        logger.info("Stopping voice session for project %s", self.project_id)
        self._active = False
        await self._cleanup()
        logger.info("Voice session stopped for project %s", self.project_id)

    async def _cleanup(self):
        """Clean up all resources with timeout protection."""
        cleanup_timeout = 5.0  # seconds

        if self._sse_task:
            self._sse_task.cancel()
            try:
                await asyncio.wait_for(self._sse_task, timeout=cleanup_timeout)
            except asyncio.CancelledError:
                logger.debug("SSE task cancelled")
            except asyncio.TimeoutError:
                logger.warning("SSE task cancellation timed out after %.1fs", cleanup_timeout)
            except Exception as e:
                logger.warning("Error while cancelling SSE task: %s", e)
            self._sse_task = None

        if self._agent:
            try:
                await asyncio.wait_for(
                    self._agent.disconnect(), timeout=cleanup_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Voice agent disconnect timed out after %.1fs", cleanup_timeout
                )
            except Exception as e:
                logger.warning("Error while disconnecting voice agent: %s", e)
            self._agent = None

        if self._eigent:
            try:
                await asyncio.wait_for(
                    self._eigent.__aexit__(None, None, None), timeout=cleanup_timeout
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Eigent client close timed out after %.1fs", cleanup_timeout
                )
            except Exception as e:
                logger.warning("Error while closing Eigent client: %s", e)
            self._eigent = None

    async def send_audio(self, audio_bytes: bytes):
        """Send audio from user to voice agent.

        Args:
            audio_bytes: Raw audio data to send.
        """
        if not self._active:
            logger.warning("Cannot send audio: session not active")
            return

        if not self._agent:
            logger.warning("Cannot send audio: voice agent not initialized")
            return

        await self._agent.send_audio(audio_bytes)

    # Internal handlers
    def _handle_transcript(self, text: str):
        """Handle transcribed user speech."""
        logger.debug("User speech transcript: %s", text[:100] if len(text) > 100 else text)
        if self.on_user_speech:
            try:
                self.on_user_speech(text)
            except Exception as e:
                logger.error("Error in on_user_speech callback: %s", e, exc_info=True)

    def _handle_agent_response(self, text: str):
        """Handle agent text response."""
        logger.debug("Agent response: %s", text[:100] if len(text) > 100 else text)
        if self.on_agent_speech:
            try:
                self.on_agent_speech(text)
            except Exception as e:
                logger.error("Error in on_agent_speech callback: %s", e, exc_info=True)

    def _handle_audio(self, audio_bytes: bytes):
        """Handle TTS audio from agent."""
        logger.debug("Received TTS audio: %d bytes", len(audio_bytes))
        if self.on_audio_out:
            try:
                self.on_audio_out(audio_bytes)
            except Exception as e:
                logger.error("Error in on_audio_out callback: %s", e, exc_info=True)

    def _handle_user_started_speaking(self):
        """Handle user barge-in (started speaking while agent was talking)."""
        logger.debug("User started speaking (barge-in)")
        if self.on_user_started_speaking:
            try:
                self.on_user_started_speaking()
            except Exception as e:
                logger.error("Error in on_user_started_speaking callback: %s", e, exc_info=True)

    def _handle_agent_started_speaking(self):
        """Handle agent starting to speak (new response)."""
        logger.debug("Agent started speaking")
        if self.on_agent_started_speaking:
            try:
                self.on_agent_started_speaking()
            except Exception as e:
                logger.error("Error in on_agent_started_speaking callback: %s", e, exc_info=True)

    async def _subscribe_events(self):
        """Subscribe to Eigent SSE events and trigger voice notifications.

        Implements reconnection with exponential backoff on failure.
        """
        logger.info("Starting SSE event subscription for project %s", self.project_id)

        backoff = 1.0  # Initial backoff in seconds
        max_backoff = 30.0  # Maximum backoff

        while self._active:
            # Store local reference to avoid race condition if _cleanup() runs concurrently
            eigent = self._eigent
            if eigent is None:
                logger.warning("SSE subscription stopped: Eigent client is None")
                break

            try:
                async for event in eigent.subscribe_events(self.project_id):
                    if not self._active:
                        logger.debug("SSE subscription stopped: session no longer active")
                        return
                    try:
                        await self._handle_sse_event(event)
                    except Exception as e:
                        logger.error(
                            "Error handling SSE event %s: %s", event.event, e, exc_info=True
                        )
                # If we exit the loop normally (stream ended), reset backoff and reconnect
                backoff = 1.0
                logger.info("SSE stream ended, reconnecting...")
            except asyncio.CancelledError:
                logger.debug("SSE subscription cancelled")
                raise
            except Exception as e:
                if not self._active:
                    break
                logger.error(
                    "SSE subscription error for project %s: %s (retrying in %.1fs)",
                    self.project_id,
                    e,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)  # Exponential backoff with cap

    async def _handle_sse_event(self, event: SSEEvent):
        """Handle SSE event and decide whether to notify user.

        Args:
            event: The SSE event received from Eigent.
        """
        logger.debug("Handling SSE event: %s with data: %s", event.event, event.data)

        # Events that trigger voice notification
        if event.event == "task_state":
            state = event.data.get("state")
            if state == "completed":
                # Check if all tasks done or just subtask
                try:
                    status = await self._eigent.get_task_status(self.project_id)
                    if status.completed == status.total:
                        await self._notify_user(
                            f"All done. {status.total} tasks completed."
                        )
                    else:
                        await self._notify_user(
                            f"{status.completed} of {status.total} done."
                        )
                except Exception as e:
                    logger.error("Failed to get task status after completion: %s", e)
                    await self._notify_user("A task completed.")
            elif state == "failed":
                await self._notify_user("A task failed. Should I retry or skip it?")

        elif event.event == "decompose_progress":
            count = event.data.get("task_count", 0)
            await self._notify_user(
                f"I've broken this into {count} tasks. Ready to start?"
            )

        elif event.event == "timeout":
            await self._notify_user("This is taking a while. Keep waiting or cancel?")

    async def _notify_user(self, message: str):
        """Send a notification message through the voice agent.

        Args:
            message: The message to speak to the user.
        """
        logger.info("Notifying user: %s", message)

        if not self._agent:
            logger.warning("Cannot notify user: voice agent not initialized")
            return

        # Use the agent's inject_message method for proper message injection
        success = await self._agent.inject_message(message)
        if not success:
            logger.warning("Failed to inject notification message to user")

    # Function handlers for Deepgram agent
    async def _fn_submit_task(self, prompt: str) -> dict:
        """Handle submit_task function call.

        Args:
            prompt: The task prompt to submit.

        Returns:
            Dict with status message.

        Note: Full task submission requires API keys and model config from the
        frontend. For now, we notify the frontend to display the task prompt.
        """
        logger.info("Function call: submit_task with prompt: %s", prompt[:100] if len(prompt) > 100 else prompt)

        # Notify frontend about the task prompt
        if self.on_task_submitted:
            try:
                self.on_task_submitted(prompt)
            except Exception as e:
                logger.error("Error in on_task_submitted callback: %s", e)

        # TODO: Full task submission requires integration with frontend's
        # API keys and model configuration. For now, return success with
        # instructions for the user.
        return {
            "status": "prompt_ready",
            "message": f"Task prompt prepared: {prompt[:100]}...",
            "instruction": "The task prompt has been sent to Eigent. Please check the main window to start execution.",
        }

    async def _fn_get_project_context(self) -> dict:
        """Handle get_project_context function call.

        Note: This endpoint is not yet implemented in the backend.
        Returns a helpful message instead.
        """
        logger.info("Function call: get_project_context")

        # TODO: Implement when backend supports this endpoint
        return {
            "message": "Project context is not available via voice. Please check the Eigent window for project details.",
            "suggestion": "You can describe what you want to do and I'll help formulate the task.",
        }

    async def _fn_get_task_status(self) -> dict:
        """Handle get_task_status function call.

        Note: This endpoint is not yet implemented in the backend.
        Returns a helpful message instead.
        """
        logger.info("Function call: get_task_status")

        # TODO: Implement when backend supports this endpoint
        return {
            "message": "Task status is not available via voice. Please check the Eigent window for progress.",
            "suggestion": "The main Eigent window shows detailed task progress and agent activity.",
        }

    async def _fn_confirm_start(self) -> dict:
        """Handle confirm_start function call.

        Note: Task confirmation happens automatically in the Eigent UI.
        """
        logger.info("Function call: confirm_start")

        # TODO: Implement when backend supports this endpoint
        return {
            "message": "Task confirmation happens in the Eigent window.",
            "suggestion": "Check the Eigent window to confirm and start the task.",
        }

    async def _fn_cancel_task(self) -> dict:
        """Handle cancel_task function call.

        Note: Use the stop button in the Eigent UI to cancel tasks.
        """
        logger.info("Function call: cancel_task")

        # TODO: Implement when backend supports this endpoint
        return {
            "message": "To cancel a task, use the stop button in the Eigent window.",
            "suggestion": "Click the stop button in the main Eigent interface.",
        }
