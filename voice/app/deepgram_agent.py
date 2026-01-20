"""Deepgram Voice Agent connection wrapper for Eigent.

This module bypasses the Deepgram SDK's message parsing to handle all message
types gracefully, including undocumented ones like 'History' that the SDK
doesn't support.
"""

import json
import asyncio
import logging
from typing import Callable, Optional, Any, Dict

from deepgram import AsyncDeepgramClient

logger = logging.getLogger(__name__)

from app.config import settings
from app.prompts import VOICE_AGENT_SYSTEM_PROMPT, VOICE_AGENT_GREETING


class VoiceAgent:
    """Manages Deepgram Voice Agent connection and function calling."""

    def __init__(
        self,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_agent_response: Optional[Callable[[str], None]] = None,
        on_audio: Optional[Callable[[bytes], None]] = None,
        on_user_started_speaking: Optional[Callable[[], None]] = None,
        on_agent_started_speaking: Optional[Callable[[], None]] = None,
    ):
        self.on_transcript = on_transcript
        self.on_agent_response = on_agent_response
        self.on_audio = on_audio
        self.on_user_started_speaking = on_user_started_speaking
        self.on_agent_started_speaking = on_agent_started_speaking

        self.client: Optional[AsyncDeepgramClient] = None
        self.connection = None
        self._connection_context = None
        self.functions: Dict[str, Callable] = {}
        self._listen_task: Optional[asyncio.Task] = None

    def register_function(self, name: str, handler: Callable):
        """Register a function that the voice agent can call."""
        self.functions[name] = handler

    async def connect(self):
        """Establish connection to Deepgram Voice Agent."""
        logger.info("Connecting to Deepgram Voice Agent")
        self.client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)

        # Build settings as raw dict to avoid SDK float serialization bug
        # (SDK converts sample_rate to 16000.0 but API expects 16000)
        agent_settings_dict = {
            "type": "Settings",
            "audio": {
                "input": {
                    "encoding": "linear16",
                    "sample_rate": 16000,
                },
                "output": {
                    "encoding": "linear16",
                    "sample_rate": 24000,
                    "container": "none",
                },
            },
            "agent": {
                "listen": {
                    "provider": {
                        "type": "deepgram",
                        "model": settings.deepgram_model,
                    },
                },
                "think": {
                    "provider": {
                        "type": "anthropic",
                        "model": settings.llm_model,
                    },
                    "prompt": VOICE_AGENT_SYSTEM_PROMPT,
                    "functions": self._get_function_definitions(),
                },
                "speak": {
                    "provider": {
                        "type": "deepgram",
                        "model": settings.tts_model,
                    },
                },
                "greeting": VOICE_AGENT_GREETING,
            },
        }

        # Connect to the voice agent websocket
        self._connection_context = self.client.agent.v1.connect()
        self.connection = await self._connection_context.__aenter__()

        # Send initial settings as raw JSON to avoid SDK serialization issues
        logger.info("Sending settings to Deepgram Voice Agent:")
        logger.info("  Audio input: encoding=linear16, sample_rate=16000")
        logger.info("  Audio output: encoding=linear16, sample_rate=24000")
        logger.info("  Listen provider: deepgram/%s", settings.deepgram_model)
        logger.info("  Think provider: anthropic/%s", settings.llm_model)
        logger.info("  Speak provider: deepgram/%s", settings.tts_model)
        func_names = [f["name"] for f in agent_settings_dict["agent"]["think"]["functions"]]
        logger.info("  Functions: %s", func_names)

        # Use internal _send method to bypass SDK's float conversion
        await self.connection._send(agent_settings_dict)

        # Start listening for messages in the background
        self._listen_task = asyncio.create_task(self._message_loop())
        logger.info("Connected to Deepgram Voice Agent successfully")

    def _get_function_definitions(self) -> list:
        """Return function definitions for Deepgram agent."""
        return [
            {
                "name": "submit_task",
                "description": "Submit a task to Eigent for execution",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The well-formed task prompt to submit",
                        }
                    },
                    "required": ["prompt"],
                },
            },
            {
                "name": "get_project_context",
                "description": "Get current project files and recent task history",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "get_task_status",
                "description": "Get the current status of running tasks",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "confirm_start",
                "description": "Confirm and start task execution after decomposition",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "cancel_task",
                "description": "Cancel the currently running task",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    async def _message_loop(self):
        """Process incoming messages from the voice agent.

        Uses raw websocket iteration to bypass SDK's strict Pydantic validation
        which crashes on undocumented message types like 'History'.
        """
        try:
            logger.info("Starting Deepgram message loop (raw websocket mode)")
            # Access the underlying websocket directly to bypass SDK parsing
            websocket = self.connection._websocket
            async for raw_message in websocket:
                try:
                    await self._handle_raw_message(raw_message)
                except Exception as e:
                    # Log but don't crash on individual message handling errors
                    logger.error("Error handling message: %s", e, exc_info=True)
            logger.info("Deepgram message loop ended normally")
        except asyncio.CancelledError:
            logger.debug("Message loop cancelled")
            raise
        except Exception as e:
            logger.error("Error in message loop: %s", e, exc_info=True)
            if hasattr(e, 'code'):
                logger.error("WebSocket close code: %s", e.code)
            if hasattr(e, 'reason'):
                logger.error("WebSocket close reason: %s", e.reason)

    async def _handle_raw_message(self, raw_message: Any):
        """Handle raw websocket message from Deepgram.

        Dispatches based on message type string, gracefully handling
        unknown message types instead of crashing.
        """
        # Binary messages are audio data
        if isinstance(raw_message, bytes):
            logger.debug("Received audio chunk: %d bytes", len(raw_message))
            if self.on_audio:
                self.on_audio(raw_message)
            return

        # Parse JSON message
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError as e:
            logger.warning("Failed to parse message as JSON: %s", e)
            return

        msg_type = msg.get("type", "unknown")

        # Dispatch based on message type
        if msg_type == "Welcome":
            logger.info("Deepgram Welcome: request_id=%s", msg.get("request_id"))

        elif msg_type == "SettingsApplied":
            logger.info("Deepgram SettingsApplied: configuration accepted")

        elif msg_type == "ConversationText":
            role = msg.get("role")
            content = msg.get("content", "")
            logger.info("ConversationText [%s]: %s", role, content[:100] if len(content) > 100 else content)
            if role == "user" and self.on_transcript:
                self.on_transcript(content)
            elif role == "assistant" and self.on_agent_response:
                self.on_agent_response(content)

        elif msg_type == "History":
            # Echoed conversation history - just log and ignore
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            logger.debug("History [%s]: %s", role, content[:50] if content else "(empty)")

        elif msg_type == "UserStartedSpeaking":
            logger.info("UserStartedSpeaking (barge-in)")
            if self.on_user_started_speaking:
                self.on_user_started_speaking()

        elif msg_type == "AgentThinking":
            content = msg.get("content", "")
            logger.info("AgentThinking: %s", content[:100] if content else "(no content)")

        elif msg_type == "AgentStartedSpeaking":
            total_latency = msg.get("total_latency", 0)
            tts_latency = msg.get("tts_latency", 0)
            ttt_latency = msg.get("ttt_latency", 0)
            logger.info("AgentStartedSpeaking: total=%.0fms, tts=%.0fms, ttt=%.0fms",
                       total_latency, tts_latency, ttt_latency)
            if self.on_agent_started_speaking:
                self.on_agent_started_speaking()

        elif msg_type == "AgentAudioDone":
            logger.info("AgentAudioDone: finished sending audio")

        elif msg_type == "FunctionCallRequest":
            await self._handle_function_call(msg)

        elif msg_type == "FunctionCallResponse":
            # Server echoing back our function response
            logger.debug("FunctionCallResponse echo: %s", msg.get("name"))

        elif msg_type == "PromptUpdated":
            logger.info("PromptUpdated: prompt change confirmed")

        elif msg_type == "SpeakUpdated":
            logger.info("SpeakUpdated: speak config change confirmed")

        elif msg_type == "InjectionRefused":
            logger.warning("InjectionRefused: %s", msg.get("message"))

        elif msg_type == "Error":
            logger.error("Deepgram Error: [%s] %s", msg.get("code"), msg.get("description"))

        elif msg_type == "Warning":
            logger.warning("Deepgram Warning: [%s] %s", msg.get("code"), msg.get("description"))

        else:
            # Unknown message type - log but don't crash
            logger.warning("Unknown Deepgram message type '%s': %s", msg_type, msg)

    async def _handle_function_call(self, msg: dict):
        """Handle function call requests from agent."""
        functions = msg.get("functions", [])

        for func in functions:
            client_side = func.get("client_side", False)
            if not client_side:
                # Server-side function, skip
                continue

            func_name = func.get("name", "")
            call_id = func.get("id", "")
            arguments_str = func.get("arguments", "{}")

            # Parse function arguments
            try:
                func_args = json.loads(arguments_str) if arguments_str else {}
            except json.JSONDecodeError as e:
                logger.error("Failed to parse arguments for function %s: %s", func_name, e)
                await self._send_function_response(call_id, func_name, {"error": "Invalid function arguments"})
                continue

            if func_name in self.functions:
                try:
                    logger.info("Calling function %s with args: %s", func_name, func_args)
                    result = await self.functions[func_name](**func_args)
                    await self._send_function_response(call_id, func_name, result)
                except Exception as e:
                    logger.error("Error executing function %s: %s", func_name, e, exc_info=True)
                    await self._send_function_response(call_id, func_name, {"error": "Function execution failed"})
            else:
                logger.warning("Unknown function called: %s", func_name)
                await self._send_function_response(call_id, func_name, {"error": f"Unknown function: {func_name}"})

    async def _send_function_response(self, call_id: str, name: str, result: dict):
        """Send function call result back to agent."""
        if not self.connection:
            logger.warning("Cannot send function response for %s: connection not available", name)
            return

        response = {
            "type": "FunctionCallResponse",
            "id": call_id,
            "name": name,
            "content": json.dumps(result),
        }
        await self.connection._send(response)
        logger.debug("Sent function response for %s", name)

    async def send_audio(self, audio_bytes: bytes):
        """Send audio data to Deepgram for processing."""
        if self.connection:
            await self.connection.send_media(audio_bytes)

    async def inject_message(self, message: str) -> bool:
        """Inject a message for the agent to speak.

        This uses the Deepgram agent's inject capability to have the agent
        speak a message to the user.

        Args:
            message: The message text for the agent to speak.

        Returns:
            True if the message was successfully injected, False otherwise.
        """
        if not self.connection:
            logger.warning("Cannot inject message: connection not available")
            return False

        try:
            # Try to use the agent's inject capability if available
            if hasattr(self.connection, 'send_inject'):
                await self.connection.send_inject({"text": message})
                return True
            elif hasattr(self.connection, 'inject'):
                await self.connection.inject(message)
                return True
            else:
                # TODO: Deepgram SDK may not expose message injection yet.
                # This is a placeholder for when the API becomes available.
                logger.warning(
                    "inject_message: Deepgram connection does not support message injection. "
                    "Message not sent: %s", message[:100] if len(message) > 100 else message
                )
                return False
        except Exception as e:
            logger.error("Failed to inject message: %s", e, exc_info=True)
            return False

    async def disconnect(self):
        """Close the Deepgram connection."""
        logger.info("Disconnecting from Deepgram Voice Agent")
        try:
            if self._listen_task:
                self._listen_task.cancel()
                try:
                    await self._listen_task
                except asyncio.CancelledError:
                    pass

            if self._connection_context:
                await self._connection_context.__aexit__(None, None, None)
        except Exception as e:
            logger.error("Error during disconnect: %s", e, exc_info=True)
        finally:
            # Always reset all connection state
            self._listen_task = None
            self._connection_context = None
            self.connection = None
            self.client = None
            logger.debug("Disconnect cleanup complete")
