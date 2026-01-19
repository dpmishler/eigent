"""Deepgram Voice Agent connection wrapper for Eigent."""

import json
import asyncio
from typing import Callable, Optional, Any, Dict

from deepgram import AsyncDeepgramClient
from deepgram.agent import (
    AgentV1Settings,
    AgentV1SettingsAgent,
    AgentV1SettingsAgentThink,
    AgentV1SettingsAgentSpeak,
    AgentV1SettingsAudio,
    AgentV1SettingsAudioInput,
    AgentV1SettingsAudioOutput,
    AgentV1SettingsAgentThinkFunctionsItem,
    AgentV1FunctionCallRequest,
    AgentV1ConversationText,
    AgentV1SendFunctionCallResponse,
)
from deepgram.agent.v1 import (
    AgentV1SettingsAgentThinkProvider_OpenAi,
    AgentV1SettingsAgentSpeakEndpointProvider_Deepgram,
)
from deepgram.core.events import EventType

from app.config import settings
from app.prompts import VOICE_AGENT_SYSTEM_PROMPT, VOICE_AGENT_GREETING


class VoiceAgent:
    """Manages Deepgram Voice Agent connection and function calling."""

    def __init__(
        self,
        on_transcript: Optional[Callable[[str], None]] = None,
        on_agent_response: Optional[Callable[[str], None]] = None,
        on_audio: Optional[Callable[[bytes], None]] = None,
    ):
        self.on_transcript = on_transcript
        self.on_agent_response = on_agent_response
        self.on_audio = on_audio

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
        self.client = AsyncDeepgramClient(api_key=settings.deepgram_api_key)

        # Build settings using the SDK's typed models
        agent_settings = AgentV1Settings(
            audio=AgentV1SettingsAudio(
                input=AgentV1SettingsAudioInput(
                    encoding="linear16",
                    sample_rate=16000,
                ),
                output=AgentV1SettingsAudioOutput(
                    encoding="linear16",
                    sample_rate=24000,
                    container="none",
                ),
            ),
            agent=AgentV1SettingsAgent(
                listen=None,  # Use defaults
                think=AgentV1SettingsAgentThink(
                    provider=AgentV1SettingsAgentThinkProvider_OpenAi(
                        model=settings.llm_model,
                    ),
                    prompt=VOICE_AGENT_SYSTEM_PROMPT,
                    functions=self._get_function_definitions(),
                ),
                speak=AgentV1SettingsAgentSpeak(
                    provider=AgentV1SettingsAgentSpeakEndpointProvider_Deepgram(
                        model=settings.tts_model,
                    ),
                ),
                greeting=VOICE_AGENT_GREETING,
            ),
        )

        # Connect to the voice agent websocket
        self._connection_context = self.client.agent.v1.connect()
        self.connection = await self._connection_context.__aenter__()

        # Send initial settings
        await self.connection.send_settings(agent_settings)

        # Start listening for messages in the background
        self._listen_task = asyncio.create_task(self._message_loop())

    def _get_function_definitions(self) -> list:
        """Return function definitions for Deepgram agent."""
        return [
            AgentV1SettingsAgentThinkFunctionsItem(
                name="submit_task",
                description="Submit a task to Eigent for execution",
                parameters={
                    "type": "object",
                    "properties": {
                        "prompt": {
                            "type": "string",
                            "description": "The well-formed task prompt to submit",
                        }
                    },
                    "required": ["prompt"],
                },
            ),
            AgentV1SettingsAgentThinkFunctionsItem(
                name="get_project_context",
                description="Get current project files and recent task history",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
            AgentV1SettingsAgentThinkFunctionsItem(
                name="get_task_status",
                description="Get the current status of running tasks",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
            AgentV1SettingsAgentThinkFunctionsItem(
                name="confirm_start",
                description="Confirm and start task execution after decomposition",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
            AgentV1SettingsAgentThinkFunctionsItem(
                name="cancel_task",
                description="Cancel the currently running task",
                parameters={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    async def _message_loop(self):
        """Process incoming messages from the voice agent."""
        try:
            async for message in self.connection:
                await self._handle_message(message)
        except Exception as e:
            # Connection closed or error occurred
            pass

    async def _handle_message(self, message: Any):
        """Handle incoming message from Deepgram."""
        if isinstance(message, bytes):
            # Audio data from TTS
            if self.on_audio:
                self.on_audio(message)
        elif isinstance(message, AgentV1ConversationText):
            # Transcript or agent response
            if message.role == "user" and self.on_transcript:
                self.on_transcript(message.content)
            elif message.role == "assistant" and self.on_agent_response:
                self.on_agent_response(message.content)
        elif isinstance(message, AgentV1FunctionCallRequest):
            # Function call request
            await self._handle_function_call(message)

    async def _handle_function_call(self, request: AgentV1FunctionCallRequest):
        """Handle function call requests from agent."""
        for func in request.functions:
            if not func.client_side:
                # Server-side function, skip
                continue

            func_name = func.name
            func_args = json.loads(func.arguments) if func.arguments else {}
            call_id = func.id

            if func_name in self.functions:
                try:
                    result = await self.functions[func_name](**func_args)
                    await self._send_function_response(call_id, func_name, result)
                except Exception as e:
                    await self._send_function_response(
                        call_id, func_name, {"error": str(e)}
                    )
            else:
                await self._send_function_response(
                    call_id, func_name, {"error": f"Unknown function: {func_name}"}
                )

    async def _send_function_response(
        self, call_id: str, name: str, result: dict
    ):
        """Send function call result back to agent."""
        response = AgentV1SendFunctionCallResponse(
            id=call_id,
            name=name,
            content=json.dumps(result),
        )
        await self.connection.send_function_call_response(response)

    async def send_audio(self, audio_bytes: bytes):
        """Send audio data to Deepgram for processing."""
        if self.connection:
            await self.connection.send_media(audio_bytes)

    async def disconnect(self):
        """Close the Deepgram connection."""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        if self._connection_context:
            await self._connection_context.__aexit__(None, None, None)
            self._connection_context = None
            self.connection = None
