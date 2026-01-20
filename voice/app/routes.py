"""WebSocket routes for voice streaming."""

import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from typing import Optional

from app.session import VoiceSession

logger = logging.getLogger(__name__)

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
    logger.info(
        "WebSocket connection accepted: session_id=%s, project_id=%s",
        session_id,
        project_id,
    )

    # Track pending tasks to ensure proper cleanup
    pending_tasks: list[asyncio.Task] = []

    # Create callbacks that send to websocket
    async def send_json(data: dict):
        try:
            await websocket.send_text(json.dumps(data))
        except Exception as e:
            logger.error(
                "Failed to send JSON to WebSocket (session=%s): %s",
                session_id,
                e,
            )

    async def send_audio(audio: bytes):
        try:
            await websocket.send_bytes(audio)
        except Exception as e:
            logger.error(
                "Failed to send audio to WebSocket (session=%s): %s",
                session_id,
                e,
            )

    def on_user_speech(text: str):
        task = asyncio.create_task(
            send_json(
                {
                    "type": "user_transcript",
                    "text": text,
                }
            )
        )
        pending_tasks.append(task)
        task.add_done_callback(lambda t: pending_tasks.remove(t) if t in pending_tasks else None)

    def on_agent_speech(text: str):
        task = asyncio.create_task(
            send_json(
                {
                    "type": "agent_transcript",
                    "text": text,
                }
            )
        )
        pending_tasks.append(task)
        task.add_done_callback(lambda t: pending_tasks.remove(t) if t in pending_tasks else None)

    def on_audio_out(audio: bytes):
        task = asyncio.create_task(send_audio(audio))
        pending_tasks.append(task)
        task.add_done_callback(lambda t: pending_tasks.remove(t) if t in pending_tasks else None)

    def on_task_submitted(prompt: str):
        task = asyncio.create_task(
            send_json(
                {
                    "type": "task_submitted",
                    "prompt": prompt,
                }
            )
        )
        pending_tasks.append(task)
        task.add_done_callback(lambda t: pending_tasks.remove(t) if t in pending_tasks else None)

    def on_user_started_speaking():
        """User started speaking - signal barge-in to stop agent audio."""
        task = asyncio.create_task(
            send_json({"type": "user_started_speaking"})
        )
        pending_tasks.append(task)
        task.add_done_callback(lambda t: pending_tasks.remove(t) if t in pending_tasks else None)

    def on_agent_started_speaking():
        """Agent started speaking - signal to resume accepting audio."""
        task = asyncio.create_task(
            send_json({"type": "agent_started_speaking"})
        )
        pending_tasks.append(task)
        task.add_done_callback(lambda t: pending_tasks.remove(t) if t in pending_tasks else None)

    # Create and start session
    session = VoiceSession(
        project_id=project_id,
        auth_token=auth_token,
        on_user_speech=on_user_speech,
        on_agent_speech=on_agent_speech,
        on_audio_out=on_audio_out,
        on_task_submitted=on_task_submitted,
        on_user_started_speaking=on_user_started_speaking,
        on_agent_started_speaking=on_agent_started_speaking,
    )

    sessions[session_id] = session

    try:
        await session.start()
        logger.info("Voice session started: session_id=%s", session_id)

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
                try:
                    msg = json.loads(data["text"])
                    msg_type = msg.get("type")
                    logger.debug(
                        "Received control message: session_id=%s, type=%s",
                        session_id,
                        msg_type,
                    )
                    if msg_type == "stop":
                        logger.info(
                            "Stop command received: session_id=%s", session_id
                        )
                        break
                except json.JSONDecodeError as e:
                    logger.warning(
                        "Invalid JSON in control message (session=%s): %s",
                        session_id,
                        e,
                    )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session_id=%s", session_id)
    except Exception as e:
        logger.error(
            "Error in WebSocket handler (session=%s): %s",
            session_id,
            e,
            exc_info=True,
        )
    finally:
        logger.info("Cleaning up session: session_id=%s", session_id)

        # Cancel any pending send tasks
        for task in pending_tasks:
            if not task.done():
                task.cancel()

        # Wait briefly for tasks to complete
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

        await session.stop()
        del sessions[session_id]
        logger.info("Session cleanup complete: session_id=%s", session_id)


@router.get("/voice/sessions")
async def list_sessions():
    """List active voice sessions (for debugging)."""
    logger.debug("Listing active sessions: count=%d", len(sessions))
    return {
        "count": len(sessions),
        "sessions": list(sessions.keys()),
    }
