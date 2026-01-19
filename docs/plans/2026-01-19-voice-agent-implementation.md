# Voice Agent Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a hands-free voice interface to Eigent using Deepgram Voice Agent API as an intelligent user proxy.

**Architecture:** Separate Python voice service running as local sidecar. Connects to Deepgram for STT/LLM/TTS, and to Eigent backend via REST API and SSE. Electron handles audio capture/playback and hosts a floating voice UI panel.

**Tech Stack:** Python 3.12, FastAPI, Deepgram SDK, WebSockets (audio streaming), React (voice UI), Electron (audio I/O)

---

## Task 1: Voice Service Skeleton

**Files:**
- Create: `voice/pyproject.toml`
- Create: `voice/main.py`
- Create: `voice/app/__init__.py`
- Create: `voice/app/config.py`

**Step 1: Create voice service directory structure**

```bash
mkdir -p voice/app
```

**Step 2: Create pyproject.toml**

```toml
[project]
name = "eigent-voice"
version = "0.1.0"
description = "Voice interface for Eigent"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.32.0",
    "websockets>=13.0",
    "deepgram-sdk>=3.0.0",
    "httpx>=0.27.0",
    "httpx-sse>=0.4.0",
    "pydantic>=2.0.0",
    "python-dotenv>=1.0.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.24.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Step 3: Create config.py**

```python
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    deepgram_api_key: str = ""
    eigent_backend_url: str = "http://localhost:5001"
    voice_service_port: int = 5002

    # Deepgram Voice Agent settings
    deepgram_model: str = "nova-2"
    tts_model: str = "aura-asteria-en"
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"

    class Config:
        env_file = ".env"


settings = Settings()
```

**Step 4: Create main.py**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Eigent Voice Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "voice"}


if __name__ == "__main__":
    import uvicorn
    from app.config import settings

    uvicorn.run(app, host="0.0.0.0", port=settings.voice_service_port)
```

**Step 5: Create app/__init__.py**

```python
# Voice service app package
```

**Step 6: Run to verify**

```bash
cd voice && uv sync && uv run python main.py
# Expected: Uvicorn running on http://0.0.0.0:5002
# Test: curl http://localhost:5002/health
# Expected: {"status":"ok","service":"voice"}
```

**Step 7: Commit**

```bash
git add voice/
git commit -m "feat(voice): add voice service skeleton with FastAPI"
```

---

## Task 2: Deepgram Voice Agent Connection

**Files:**
- Create: `voice/app/deepgram_agent.py`
- Create: `voice/app/prompts.py`
- Modify: `voice/main.py`

**Step 1: Create prompts.py with system prompt**

```python
VOICE_AGENT_SYSTEM_PROMPT = """You are the voice interface for Eigent, a multi-agent AI workforce.

ROLE:
- You help users direct tasks using natural speech
- You clarify ambiguous requests before submitting to Eigent
- You announce progress milestones and results concisely

VOICE STYLE:
- Keep responses to 1-2 sentences max
- Be conversational but efficient - no filler words
- Never read code, file contents, or long lists aloud
- Summarize results, don't recite them

WORKFLOW:
1. Listen to user's request
2. If unclear, ask ONE clarifying question
3. When ready, call submit_task() with a well-formed prompt
4. Monitor progress via SSE events
5. Announce milestones: "2 of 4 done"
6. On completion, summarize: "Done. Created 3 files and deployed to staging."

CONTEXT:
- You have access to the current project via get_project_context()
- Use this to formulate specific prompts (file names, paths, etc.)
- Don't ask the user for info you can look up

DECISIONS:
- Task confirmations, errors, and failures require user input
- Routine progress just gets announced, no confirmation needed
"""

VOICE_AGENT_GREETING = "Ready. What would you like to do?"
```

**Step 2: Create deepgram_agent.py**

```python
import json
import asyncio
from typing import Callable, Optional
from deepgram import DeepgramClient, DeepgramClientOptions
from deepgram.clients.live import LiveTranscriptionEvents

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

        self.client: Optional[DeepgramClient] = None
        self.connection = None
        self.functions = {}

    def register_function(self, name: str, handler: Callable):
        """Register a function that the voice agent can call."""
        self.functions[name] = handler

    async def connect(self):
        """Establish connection to Deepgram Voice Agent."""
        config = DeepgramClientOptions(
            api_key=settings.deepgram_api_key,
        )
        self.client = DeepgramClient(config)

        # Configure voice agent settings
        options = {
            "model": settings.deepgram_model,
            "language": "en",
            "smart_format": True,
            "interim_results": True,
            "endpointing": 300,
            "vad_events": True,
            # Agent configuration
            "agent": {
                "think": {
                    "provider": {
                        "type": settings.llm_provider,
                        "model": settings.llm_model,
                    },
                    "prompt": VOICE_AGENT_SYSTEM_PROMPT,
                    "functions": self._get_function_definitions(),
                },
                "speak": {
                    "model": settings.tts_model,
                },
                "greeting": VOICE_AGENT_GREETING,
            },
        }

        self.connection = self.client.agent.websocket.v("1")
        await self.connection.start(options)

        # Set up event handlers
        self.connection.on(LiveTranscriptionEvents.Transcript, self._handle_transcript)
        self.connection.on("AgentResponse", self._handle_agent_response)
        self.connection.on("Audio", self._handle_audio)
        self.connection.on("FunctionCallRequest", self._handle_function_call)

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

    async def _handle_transcript(self, transcript_data):
        """Handle transcribed user speech."""
        transcript = transcript_data.get("channel", {}).get("alternatives", [{}])[0].get("transcript", "")
        if transcript and self.on_transcript:
            self.on_transcript(transcript)

    async def _handle_agent_response(self, response_data):
        """Handle agent text response."""
        text = response_data.get("text", "")
        if text and self.on_agent_response:
            self.on_agent_response(text)

    async def _handle_audio(self, audio_data):
        """Handle TTS audio from agent."""
        audio_bytes = audio_data.get("audio", b"")
        if audio_bytes and self.on_audio:
            self.on_audio(audio_bytes)

    async def _handle_function_call(self, function_data):
        """Handle function call requests from agent."""
        func_name = function_data.get("name")
        func_args = function_data.get("arguments", {})
        call_id = function_data.get("id")

        if func_name in self.functions:
            try:
                result = await self.functions[func_name](**func_args)
                await self._send_function_response(call_id, result)
            except Exception as e:
                await self._send_function_response(call_id, {"error": str(e)})
        else:
            await self._send_function_response(call_id, {"error": f"Unknown function: {func_name}"})

    async def _send_function_response(self, call_id: str, result: dict):
        """Send function call result back to agent."""
        await self.connection.send(json.dumps({
            "type": "FunctionCallResponse",
            "id": call_id,
            "result": json.dumps(result),
        }))

    async def send_audio(self, audio_bytes: bytes):
        """Send audio data to Deepgram for processing."""
        if self.connection:
            await self.connection.send(audio_bytes)

    async def disconnect(self):
        """Close the Deepgram connection."""
        if self.connection:
            await self.connection.finish()
            self.connection = None
```

**Step 3: Run to verify imports**

```bash
cd voice && uv run python -c "from app.deepgram_agent import VoiceAgent; print('OK')"
# Expected: OK
```

**Step 4: Commit**

```bash
git add voice/app/
git commit -m "feat(voice): add Deepgram Voice Agent connection wrapper"
```

---

## Task 3: Eigent Client

**Files:**
- Create: `voice/app/eigent_client.py`
- Create: `voice/app/models.py`

**Step 1: Create models.py**

```python
from typing import Optional, List
from pydantic import BaseModel
from enum import Enum


class TaskState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskInfo(BaseModel):
    id: str
    content: str
    state: TaskState
    result: Optional[str] = None


class ProjectContext(BaseModel):
    project_id: str
    files: List[str]
    recent_tasks: List[TaskInfo]


class TaskStatus(BaseModel):
    total: int
    completed: int
    running: int
    failed: int
    current_task: Optional[str] = None


class SSEEvent(BaseModel):
    event: str
    data: dict
```

**Step 2: Create eigent_client.py**

```python
import httpx
from typing import AsyncGenerator, Optional
from httpx_sse import aconnect_sse

from app.config import settings
from app.models import ProjectContext, TaskStatus, SSEEvent


class EigentClient:
    """Client for interacting with Eigent backend."""

    def __init__(self, auth_token: Optional[str] = None):
        self.base_url = settings.eigent_backend_url
        self.auth_token = auth_token
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        headers = {}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()

    async def submit_task(self, project_id: str, prompt: str) -> str:
        """Submit a task to Eigent and return task ID."""
        response = await self._client.post(
            "/chat",
            json={
                "project_id": project_id,
                "question": prompt,
            },
        )
        response.raise_for_status()
        return response.json().get("task_id")

    async def confirm_start(self, project_id: str) -> bool:
        """Confirm task decomposition and start execution."""
        response = await self._client.post(
            f"/chat/{project_id}/confirm",
        )
        response.raise_for_status()
        return True

    async def cancel_task(self, project_id: str) -> bool:
        """Cancel the current task."""
        response = await self._client.post(
            f"/chat/{project_id}/cancel",
        )
        response.raise_for_status()
        return True

    async def get_project_context(self, project_id: str) -> ProjectContext:
        """Get project files and recent task history."""
        response = await self._client.get(f"/project/{project_id}/context")
        response.raise_for_status()
        return ProjectContext(**response.json())

    async def get_task_status(self, project_id: str) -> TaskStatus:
        """Get current task execution status."""
        response = await self._client.get(f"/chat/{project_id}/status")
        response.raise_for_status()
        return TaskStatus(**response.json())

    async def subscribe_events(self, project_id: str) -> AsyncGenerator[SSEEvent, None]:
        """Subscribe to SSE events for a project."""
        async with aconnect_sse(
            self._client,
            "GET",
            f"/chat/{project_id}/events",
        ) as event_source:
            async for event in event_source.aiter_sse():
                yield SSEEvent(
                    event=event.event,
                    data=event.json() if event.data else {},
                )
```

**Step 3: Run to verify imports**

```bash
cd voice && uv run python -c "from app.eigent_client import EigentClient; print('OK')"
# Expected: OK
```

**Step 4: Commit**

```bash
git add voice/app/
git commit -m "feat(voice): add Eigent backend client with SSE support"
```

---

## Task 4: Voice Session Manager

**Files:**
- Create: `voice/app/session.py`
- Modify: `voice/main.py`

**Step 1: Create session.py**

```python
import asyncio
from typing import Optional, Callable
from dataclasses import dataclass, field

from app.deepgram_agent import VoiceAgent
from app.eigent_client import EigentClient
from app.models import SSEEvent, TaskStatus


@dataclass
class VoiceSession:
    """Manages a voice session including agent and Eigent connections."""

    project_id: str
    auth_token: Optional[str] = None

    # Callbacks for UI updates
    on_user_speech: Optional[Callable[[str], None]] = None
    on_agent_speech: Optional[Callable[[str], None]] = None
    on_audio_out: Optional[Callable[[bytes], None]] = None
    on_task_submitted: Optional[Callable[[str], None]] = None
    on_status_update: Optional[Callable[[TaskStatus], None]] = None

    # Internal state
    _agent: Optional[VoiceAgent] = field(default=None, init=False)
    _eigent: Optional[EigentClient] = field(default=None, init=False)
    _sse_task: Optional[asyncio.Task] = field(default=None, init=False)
    _active: bool = field(default=False, init=False)

    async def start(self):
        """Start the voice session."""
        # Initialize Eigent client
        self._eigent = EigentClient(self.auth_token)
        await self._eigent.__aenter__()

        # Initialize voice agent with callbacks
        self._agent = VoiceAgent(
            on_transcript=self._handle_transcript,
            on_agent_response=self._handle_agent_response,
            on_audio=self._handle_audio,
        )

        # Register function handlers
        self._agent.register_function("submit_task", self._fn_submit_task)
        self._agent.register_function("get_project_context", self._fn_get_project_context)
        self._agent.register_function("get_task_status", self._fn_get_task_status)
        self._agent.register_function("confirm_start", self._fn_confirm_start)
        self._agent.register_function("cancel_task", self._fn_cancel_task)

        # Connect to Deepgram
        await self._agent.connect()

        # Start SSE subscription
        self._active = True
        self._sse_task = asyncio.create_task(self._subscribe_events())

    async def stop(self):
        """Stop the voice session."""
        self._active = False

        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass

        if self._agent:
            await self._agent.disconnect()

        if self._eigent:
            await self._eigent.__aexit__(None, None, None)

    async def send_audio(self, audio_bytes: bytes):
        """Send audio from user to voice agent."""
        if self._agent:
            await self._agent.send_audio(audio_bytes)

    # Internal handlers
    def _handle_transcript(self, text: str):
        """Handle transcribed user speech."""
        if self.on_user_speech:
            self.on_user_speech(text)

    def _handle_agent_response(self, text: str):
        """Handle agent text response."""
        if self.on_agent_speech:
            self.on_agent_speech(text)

    def _handle_audio(self, audio_bytes: bytes):
        """Handle TTS audio from agent."""
        if self.on_audio_out:
            self.on_audio_out(audio_bytes)

    async def _subscribe_events(self):
        """Subscribe to Eigent SSE events and trigger voice notifications."""
        async for event in self._eigent.subscribe_events(self.project_id):
            if not self._active:
                break
            await self._handle_sse_event(event)

    async def _handle_sse_event(self, event: SSEEvent):
        """Handle SSE event and decide whether to notify user."""
        # Events that trigger voice notification
        if event.event == "task_state":
            state = event.data.get("state")
            if state == "completed":
                # Check if all tasks done or just subtask
                status = await self._eigent.get_task_status(self.project_id)
                if status.completed == status.total:
                    await self._notify_user(f"All done. {status.total} tasks completed.")
                else:
                    await self._notify_user(f"{status.completed} of {status.total} done.")
            elif state == "failed":
                await self._notify_user("A task failed. Should I retry or skip it?")

        elif event.event == "decompose_progress":
            count = event.data.get("task_count", 0)
            await self._notify_user(f"I've broken this into {count} tasks. Ready to start?")

        elif event.event == "timeout":
            await self._notify_user("This is taking a while. Keep waiting or cancel?")

    async def _notify_user(self, message: str):
        """Send a notification message through the voice agent."""
        # Inject message for agent to speak
        if self._agent and self._agent.connection:
            await self._agent.connection.send_text(message)

    # Function handlers for Deepgram agent
    async def _fn_submit_task(self, prompt: str) -> dict:
        """Handle submit_task function call."""
        task_id = await self._eigent.submit_task(self.project_id, prompt)
        if self.on_task_submitted:
            self.on_task_submitted(prompt)
        return {"status": "submitted", "task_id": task_id}

    async def _fn_get_project_context(self) -> dict:
        """Handle get_project_context function call."""
        context = await self._eigent.get_project_context(self.project_id)
        return context.model_dump()

    async def _fn_get_task_status(self) -> dict:
        """Handle get_task_status function call."""
        status = await self._eigent.get_task_status(self.project_id)
        if self.on_status_update:
            self.on_status_update(status)
        return status.model_dump()

    async def _fn_confirm_start(self) -> dict:
        """Handle confirm_start function call."""
        await self._eigent.confirm_start(self.project_id)
        return {"status": "started"}

    async def _fn_cancel_task(self) -> dict:
        """Handle cancel_task function call."""
        await self._eigent.cancel_task(self.project_id)
        return {"status": "cancelled"}
```

**Step 2: Run to verify imports**

```bash
cd voice && uv run python -c "from app.session import VoiceSession; print('OK')"
# Expected: OK
```

**Step 3: Commit**

```bash
git add voice/app/
git commit -m "feat(voice): add VoiceSession manager with function handlers"
```

---

## Task 5: WebSocket Audio Endpoint

**Files:**
- Create: `voice/app/routes.py`
- Modify: `voice/main.py`

**Step 1: Create routes.py**

```python
import asyncio
import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional

from app.session import VoiceSession

router = APIRouter()

# Active sessions by connection ID
sessions: dict[str, VoiceSession] = {}


@router.websocket("/voice/stream")
async def voice_stream(
    websocket: WebSocket,
    project_id: str = Query(...),
    auth_token: Optional[str] = Query(None),
):
    """
    WebSocket endpoint for bidirectional audio streaming.

    Client sends: binary audio data (16-bit PCM, 16kHz)
    Server sends:
      - binary audio data (TTS output)
      - JSON messages for transcripts and events
    """
    await websocket.accept()

    session_id = str(id(websocket))

    # Create callbacks that send to websocket
    async def send_json(data: dict):
        await websocket.send_text(json.dumps(data))

    async def send_audio(audio: bytes):
        await websocket.send_bytes(audio)

    def on_user_speech(text: str):
        asyncio.create_task(send_json({
            "type": "user_transcript",
            "text": text,
        }))

    def on_agent_speech(text: str):
        asyncio.create_task(send_json({
            "type": "agent_transcript",
            "text": text,
        }))

    def on_audio_out(audio: bytes):
        asyncio.create_task(send_audio(audio))

    def on_task_submitted(prompt: str):
        asyncio.create_task(send_json({
            "type": "task_submitted",
            "prompt": prompt,
        }))

    # Create and start session
    session = VoiceSession(
        project_id=project_id,
        auth_token=auth_token,
        on_user_speech=on_user_speech,
        on_agent_speech=on_agent_speech,
        on_audio_out=on_audio_out,
        on_task_submitted=on_task_submitted,
    )

    sessions[session_id] = session

    try:
        await session.start()

        # Send ready message
        await send_json({"type": "ready"})

        # Receive audio from client
        while True:
            data = await websocket.receive()

            if "bytes" in data:
                # Audio data from microphone
                await session.send_audio(data["bytes"])
            elif "text" in data:
                # Control message
                msg = json.loads(data["text"])
                if msg.get("type") == "stop":
                    break

    except WebSocketDisconnect:
        pass
    finally:
        await session.stop()
        del sessions[session_id]


@router.get("/voice/sessions")
async def list_sessions():
    """List active voice sessions (for debugging)."""
    return {
        "count": len(sessions),
        "sessions": list(sessions.keys()),
    }
```

**Step 2: Update main.py to include routes**

```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.routes import router as voice_router

app = FastAPI(title="Eigent Voice Service")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(voice_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "voice"}


if __name__ == "__main__":
    import uvicorn
    from app.config import settings

    uvicorn.run(app, host="0.0.0.0", port=settings.voice_service_port)
```

**Step 3: Run to verify**

```bash
cd voice && uv run python main.py &
sleep 2
curl http://localhost:5002/health
# Expected: {"status":"ok","service":"voice"}
curl http://localhost:5002/voice/sessions
# Expected: {"count":0,"sessions":[]}
kill %1
```

**Step 4: Commit**

```bash
git add voice/
git commit -m "feat(voice): add WebSocket audio streaming endpoint"
```

---

## Task 6: Electron Audio Capture

**Files:**
- Create: `electron/main/voice.ts`
- Modify: `electron/main/index.ts`

**Step 1: Create voice.ts**

```typescript
import { ipcMain, BrowserWindow } from 'electron';

let voiceServicePort = 5002;
let activeVoiceWindow: BrowserWindow | null = null;

export function setupVoiceHandlers(mainWindow: BrowserWindow) {
  // Set voice service port
  ipcMain.handle('voice-set-port', (_, port: number) => {
    voiceServicePort = port;
    return true;
  });

  // Get voice service URL
  ipcMain.handle('voice-get-url', () => {
    return `ws://localhost:${voiceServicePort}/voice/stream`;
  });

  // Create floating voice panel window
  ipcMain.handle('voice-open-panel', () => {
    if (activeVoiceWindow) {
      activeVoiceWindow.focus();
      return;
    }

    activeVoiceWindow = new BrowserWindow({
      width: 320,
      height: 200,
      frame: false,
      transparent: true,
      alwaysOnTop: true,
      resizable: true,
      minimizable: false,
      maximizable: false,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        preload: mainWindow.webContents.getWebPreferences().preload,
      },
    });

    // Position in bottom-right corner
    const { screen } = require('electron');
    const display = screen.getPrimaryDisplay();
    const { width, height } = display.workAreaSize;
    activeVoiceWindow.setPosition(width - 340, height - 220);

    // Load voice panel UI
    if (process.env.VITE_DEV_SERVER_URL) {
      activeVoiceWindow.loadURL(`${process.env.VITE_DEV_SERVER_URL}#/voice-panel`);
    } else {
      activeVoiceWindow.loadFile('dist/index.html', { hash: '/voice-panel' });
    }

    activeVoiceWindow.on('closed', () => {
      activeVoiceWindow = null;
    });
  });

  // Close voice panel
  ipcMain.handle('voice-close-panel', () => {
    if (activeVoiceWindow) {
      activeVoiceWindow.close();
      activeVoiceWindow = null;
    }
  });

  // Pop out to separate window
  ipcMain.handle('voice-pop-out', () => {
    if (activeVoiceWindow) {
      activeVoiceWindow.setSize(400, 500);
      activeVoiceWindow.setAlwaysOnTop(false);
      activeVoiceWindow.setResizable(true);
      activeVoiceWindow.center();
    }
  });

  // Pop back to floating overlay
  ipcMain.handle('voice-pop-in', () => {
    if (activeVoiceWindow) {
      const { screen } = require('electron');
      const display = screen.getPrimaryDisplay();
      const { width, height } = display.workAreaSize;

      activeVoiceWindow.setSize(320, 200);
      activeVoiceWindow.setAlwaysOnTop(true);
      activeVoiceWindow.setPosition(width - 340, height - 220);
    }
  });
}
```

**Step 2: Update electron/main/index.ts to include voice handlers**

Add import at top:
```typescript
import { setupVoiceHandlers } from './voice';
```

Add after window creation (find `createWindow` function, add near end):
```typescript
setupVoiceHandlers(mainWindow);
```

**Step 3: Commit**

```bash
git add electron/main/
git commit -m "feat(voice): add Electron voice panel window management"
```

---

## Task 7: Voice Panel React Component

**Files:**
- Create: `src/pages/VoicePanel.tsx`
- Create: `src/hooks/useVoiceSession.ts`
- Modify: `src/routers/index.tsx`

**Step 1: Create useVoiceSession.ts hook**

```typescript
import { useState, useEffect, useCallback, useRef } from 'react';

interface VoiceMessage {
  id: string;
  type: 'user' | 'agent';
  text: string;
  timestamp: Date;
}

interface UseVoiceSessionOptions {
  projectId: string;
  authToken?: string;
  onTaskSubmitted?: (prompt: string) => void;
}

export function useVoiceSession({ projectId, authToken, onTaskSubmitted }: UseVoiceSessionOptions) {
  const [isConnected, setIsConnected] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [messages, setMessages] = useState<VoiceMessage[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);

  const connect = useCallback(async () => {
    try {
      // Get voice service URL from Electron
      const url = await window.ipcRenderer.invoke('voice-get-url');
      const wsUrl = `${url}?project_id=${projectId}${authToken ? `&auth_token=${authToken}` : ''}`;

      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        setError(null);
      };

      ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
          const msg = JSON.parse(event.data);

          if (msg.type === 'user_transcript') {
            setMessages(prev => [...prev, {
              id: crypto.randomUUID(),
              type: 'user',
              text: msg.text,
              timestamp: new Date(),
            }]);
          } else if (msg.type === 'agent_transcript') {
            setMessages(prev => [...prev, {
              id: crypto.randomUUID(),
              type: 'agent',
              text: msg.text,
              timestamp: new Date(),
            }]);
          } else if (msg.type === 'task_submitted') {
            onTaskSubmitted?.(msg.prompt);
          }
        } else if (event.data instanceof Blob) {
          // Audio data - play it
          playAudio(event.data);
        }
      };

      ws.onerror = () => {
        setError('Connection error');
      };

      ws.onclose = () => {
        setIsConnected(false);
        setIsListening(false);
      };

    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to connect');
    }
  }, [projectId, authToken, onTaskSubmitted]);

  const disconnect = useCallback(() => {
    if (wsRef.current) {
      wsRef.current.send(JSON.stringify({ type: 'stop' }));
      wsRef.current.close();
      wsRef.current = null;
    }
    stopMicrophone();
    setIsConnected(false);
    setIsListening(false);
  }, []);

  const startMicrophone = useCallback(async () => {
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: 16000,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
        }
      });

      mediaStreamRef.current = stream;
      audioContextRef.current = new AudioContext({ sampleRate: 16000 });

      const source = audioContextRef.current.createMediaStreamSource(stream);
      const processor = audioContextRef.current.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (e) => {
        if (wsRef.current?.readyState === WebSocket.OPEN) {
          const inputData = e.inputBuffer.getChannelData(0);
          const pcmData = new Int16Array(inputData.length);

          for (let i = 0; i < inputData.length; i++) {
            pcmData[i] = Math.max(-32768, Math.min(32767, inputData[i] * 32768));
          }

          wsRef.current.send(pcmData.buffer);
        }
      };

      source.connect(processor);
      processor.connect(audioContextRef.current.destination);

      setIsListening(true);
    } catch (err) {
      setError('Microphone access denied');
    }
  }, []);

  const stopMicrophone = useCallback(() => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach(track => track.stop());
      mediaStreamRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    setIsListening(false);
  }, []);

  const playAudio = async (blob: Blob) => {
    const arrayBuffer = await blob.arrayBuffer();
    const audioContext = new AudioContext();
    const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
    const source = audioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(audioContext.destination);
    source.start();
  };

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      disconnect();
    };
  }, [disconnect]);

  return {
    isConnected,
    isListening,
    messages,
    error,
    connect,
    disconnect,
    startMicrophone,
    stopMicrophone,
  };
}
```

**Step 2: Create VoicePanel.tsx**

```typescript
import React, { useEffect } from 'react';
import { useVoiceSession } from '@/hooks/useVoiceSession';
import { useProjectStore } from '@/store/projectStore';
import { Mic, MicOff, X, Maximize2, Minimize2 } from 'lucide-react';

export default function VoicePanel() {
  const { currentProjectId } = useProjectStore();
  const [isExpanded, setIsExpanded] = React.useState(false);

  const {
    isConnected,
    isListening,
    messages,
    error,
    connect,
    disconnect,
    startMicrophone,
    stopMicrophone,
  } = useVoiceSession({
    projectId: currentProjectId || '',
    onTaskSubmitted: (prompt) => {
      // Could dispatch to chat store here
      console.log('Task submitted:', prompt);
    },
  });

  // Auto-connect when panel opens
  useEffect(() => {
    if (currentProjectId) {
      connect().then(() => startMicrophone());
    }
    return () => disconnect();
  }, [currentProjectId]);

  const handleClose = () => {
    disconnect();
    window.ipcRenderer.invoke('voice-close-panel');
  };

  const handleToggleExpand = () => {
    if (isExpanded) {
      window.ipcRenderer.invoke('voice-pop-in');
    } else {
      window.ipcRenderer.invoke('voice-pop-out');
    }
    setIsExpanded(!isExpanded);
  };

  const handleToggleMic = () => {
    if (isListening) {
      stopMicrophone();
    } else {
      startMicrophone();
    }
  };

  // Get last few messages for compact view
  const recentMessages = messages.slice(-3);

  return (
    <div className="flex flex-col h-full bg-surface-primary rounded-xl border border-border-secondary overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-surface-secondary border-b border-border-secondary drag-region">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-500' : 'bg-red-500'}`} />
          <span className="text-sm font-medium text-text-primary">Voice Active</span>
        </div>
        <div className="flex items-center gap-1 no-drag">
          <button
            onClick={handleToggleExpand}
            className="p-1 hover:bg-surface-hover rounded"
          >
            {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
          <button
            onClick={handleClose}
            className="p-1 hover:bg-surface-hover rounded"
          >
            <X size={14} />
          </button>
        </div>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {error && (
          <div className="text-red-500 text-sm">{error}</div>
        )}
        {recentMessages.map((msg) => (
          <div
            key={msg.id}
            className={`text-sm ${
              msg.type === 'user'
                ? 'text-text-secondary italic'
                : 'text-text-primary'
            }`}
          >
            {msg.type === 'user' ? 'You: ' : 'Agent: '}
            {msg.text}
          </div>
        ))}
      </div>

      {/* Controls */}
      <div className="flex items-center justify-center gap-2 p-3 border-t border-border-secondary">
        <button
          onClick={handleToggleMic}
          className={`p-3 rounded-full ${
            isListening
              ? 'bg-red-500 hover:bg-red-600'
              : 'bg-surface-secondary hover:bg-surface-hover'
          }`}
        >
          {isListening ? <MicOff size={20} /> : <Mic size={20} />}
        </button>
      </div>
    </div>
  );
}
```

**Step 3: Add route in src/routers/index.tsx**

Add import:
```typescript
import VoicePanel from '@/pages/VoicePanel';
```

Add route in the routes array:
```typescript
{
  path: '/voice-panel',
  element: <VoicePanel />,
},
```

**Step 4: Commit**

```bash
git add src/pages/VoicePanel.tsx src/hooks/useVoiceSession.ts src/routers/index.tsx
git commit -m "feat(voice): add VoicePanel UI component with audio handling"
```

---

## Task 8: Voice Activation Button

**Files:**
- Modify: `src/components/ChatBox/BottomBox/InputBox.tsx`

**Step 1: Add voice button to InputBox**

Find the input area in InputBox.tsx and add a microphone button:

```typescript
// Add import
import { Mic } from 'lucide-react';

// Add handler function
const handleVoiceClick = async () => {
  await window.ipcRenderer.invoke('voice-open-panel');
};

// Add button next to send button (find the button group)
<button
  onClick={handleVoiceClick}
  className="p-2 hover:bg-surface-hover rounded-lg"
  title="Voice mode (Cmd+Shift+V)"
>
  <Mic size={20} />
</button>
```

**Step 2: Add keyboard shortcut in Electron**

In `electron/main/voice.ts`, add to `setupVoiceHandlers`:

```typescript
import { globalShortcut } from 'electron';

// Register global shortcut
globalShortcut.register('CommandOrControl+Shift+V', () => {
  if (activeVoiceWindow) {
    activeVoiceWindow.close();
  } else {
    // Trigger voice-open-panel
    mainWindow.webContents.send('voice-toggle');
  }
});
```

**Step 3: Commit**

```bash
git add src/components/ChatBox/BottomBox/InputBox.tsx electron/main/voice.ts
git commit -m "feat(voice): add voice activation button and keyboard shortcut"
```

---

## Task 9: Voice Service Startup in Electron

**Files:**
- Create: `electron/main/voiceService.ts`
- Modify: `electron/main/init.ts`

**Step 1: Create voiceService.ts**

```typescript
import { spawn, ChildProcess } from 'child_process';
import path from 'path';
import { app } from 'electron';
import { findAvailablePort } from './utils';

let voiceProcess: ChildProcess | null = null;
let voicePort: number | null = null;

export async function startVoiceService(): Promise<number> {
  // Find available port
  voicePort = await findAvailablePort(5002);

  const voicePath = app.isPackaged
    ? path.join(process.resourcesPath, 'voice')
    : path.join(__dirname, '../../voice');

  // Spawn voice service
  voiceProcess = spawn('uv', ['run', 'python', 'main.py'], {
    cwd: voicePath,
    env: {
      ...process.env,
      VOICE_SERVICE_PORT: String(voicePort),
    },
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  voiceProcess.stdout?.on('data', (data) => {
    console.log(`[voice] ${data}`);
  });

  voiceProcess.stderr?.on('data', (data) => {
    console.error(`[voice] ${data}`);
  });

  // Wait for service to be ready
  await waitForService(voicePort);

  return voicePort;
}

export function stopVoiceService() {
  if (voiceProcess) {
    voiceProcess.kill();
    voiceProcess = null;
  }
}

async function waitForService(port: number, timeout = 30000): Promise<void> {
  const start = Date.now();
  while (Date.now() - start < timeout) {
    try {
      const response = await fetch(`http://localhost:${port}/health`);
      if (response.ok) return;
    } catch {
      // Not ready yet
    }
    await new Promise(r => setTimeout(r, 500));
  }
  throw new Error('Voice service failed to start');
}

export function getVoicePort(): number | null {
  return voicePort;
}
```

**Step 2: Update init.ts to start voice service**

Add imports:
```typescript
import { startVoiceService, stopVoiceService } from './voiceService';
```

In the initialization flow, after backend starts:
```typescript
// Start voice service
const voicePort = await startVoiceService();
console.log(`Voice service started on port ${voicePort}`);
```

In cleanup/quit handler:
```typescript
stopVoiceService();
```

**Step 3: Commit**

```bash
git add electron/main/voiceService.ts electron/main/init.ts
git commit -m "feat(voice): add voice service startup in Electron"
```

---

## Task 10: Integration Testing

**Files:**
- Create: `voice/tests/test_session.py`
- Create: `voice/tests/conftest.py`

**Step 1: Create conftest.py**

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_deepgram():
    mock = MagicMock()
    mock.agent.websocket.v.return_value.start = AsyncMock()
    mock.agent.websocket.v.return_value.send = AsyncMock()
    mock.agent.websocket.v.return_value.finish = AsyncMock()
    return mock


@pytest.fixture
def mock_eigent_client():
    mock = AsyncMock()
    mock.submit_task.return_value = "task-123"
    mock.get_project_context.return_value = MagicMock(
        project_id="proj-1",
        files=["main.py", "utils.py"],
        recent_tasks=[],
    )
    mock.get_task_status.return_value = MagicMock(
        total=3,
        completed=1,
        running=1,
        failed=0,
    )
    return mock
```

**Step 2: Create test_session.py**

```python
import pytest
from unittest.mock import patch, AsyncMock

from app.session import VoiceSession


@pytest.mark.asyncio
async def test_session_start_and_stop(mock_deepgram, mock_eigent_client):
    with patch('app.session.VoiceAgent') as MockAgent, \
         patch('app.session.EigentClient') as MockClient:

        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_eigent_client)
        MockClient.return_value.__aexit__ = AsyncMock()

        mock_agent = AsyncMock()
        MockAgent.return_value = mock_agent

        session = VoiceSession(project_id="test-project")

        await session.start()

        assert mock_agent.connect.called
        assert mock_agent.register_function.call_count == 5  # 5 functions

        await session.stop()

        assert mock_agent.disconnect.called


@pytest.mark.asyncio
async def test_submit_task_function(mock_eigent_client):
    with patch('app.session.VoiceAgent'), \
         patch('app.session.EigentClient') as MockClient:

        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_eigent_client)
        MockClient.return_value.__aexit__ = AsyncMock()

        submitted_prompt = None
        def on_submitted(prompt):
            nonlocal submitted_prompt
            submitted_prompt = prompt

        session = VoiceSession(
            project_id="test-project",
            on_task_submitted=on_submitted,
        )
        session._eigent = mock_eigent_client

        result = await session._fn_submit_task(prompt="Test task")

        assert result["status"] == "submitted"
        assert result["task_id"] == "task-123"
        assert submitted_prompt == "Test task"
```

**Step 3: Run tests**

```bash
cd voice && uv run pytest tests/ -v
# Expected: All tests pass
```

**Step 4: Commit**

```bash
git add voice/tests/
git commit -m "test(voice): add session integration tests"
```

---

## Summary

This plan covers:

1. **Voice service skeleton** - FastAPI + config
2. **Deepgram integration** - Voice agent wrapper with function calling
3. **Eigent client** - REST + SSE connection to backend
4. **Session manager** - Orchestrates voice agent + Eigent + notifications
5. **WebSocket endpoint** - Bidirectional audio streaming
6. **Electron audio** - Window management for voice panel
7. **React voice panel** - UI component with audio handling
8. **Activation button** - Entry point in existing UI
9. **Service startup** - Launch voice service with Electron
10. **Testing** - Integration tests for session logic

Each task is a focused commit. The voice service is fully isolated from the existing Eigent backend.
