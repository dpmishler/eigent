# Voice Agent for Eigent

## Overview

Voice Agent adds a hands-free voice interface to Eigent's multi-agent workforce system. Users can speak naturally to direct tasks, and the voice agent responds with concise spoken summaries while full execution details remain visible on screen.

**Core concept:** The voice agent acts as an intelligent intermediary between the user and Eigent's orchestrator. It's not simple dictation - it interprets casual speech, asks clarifying questions, and formulates well-structured prompts before submitting to Eigent.

### Example Interaction

```
User (speaking): "hey can you uh... deploy the latest changes to staging"

Voice Agent: "Deploy to staging - should I run tests first, or skip them?"

User: "run them"

Voice Agent: "Got it, deploying with tests."

→ Submits to Eigent: "Run the test suite, then deploy the application to the staging environment"

Voice Agent (later): "Tests passed. Deployment complete - staging is live."
```

### What Doesn't Change

Eigent's backend, workforce orchestration, and agent routing remain untouched. The voice service is a new client that interacts with existing APIs, just like the React UI does.

---

## Architecture

A Python-based Voice Service runs as a local sidecar alongside the Electron app.

```
┌─────────────────────────────────────────────────────────────────┐
│                        User's Machine                           │
│                                                                 │
│  ┌─────────────┐      ┌─────────────────────────────────────┐  │
│  │  Electron   │◄────►│          Voice Service              │  │
│  │    App      │audio │  ┌─────────────────────────────┐    │  │
│  │             │stream│  │   Deepgram Voice Agent      │    │  │
│  │ ┌─────────┐ │      │  │   - Speech recognition      │    │  │
│  │ │React UI │ │      │  │   - LLM (clarify, refine)   │    │  │
│  │ └─────────┘ │      │  │   - Text-to-speech          │    │  │
│  └──────┬──────┘      │  └──────────────┬──────────────┘    │  │
│         │             │                 │                    │  │
│         │             │  ┌──────────────┴──────────────┐    │  │
│         │             │  │      Eigent Client          │    │  │
│         │             │  │   - Submit prompts (REST)   │    │  │
│         │             │  │   - Subscribe events (SSE)  │    │  │
│         │             │  │   - Project context sync    │    │  │
│         │             │  └──────────────┬──────────────┘    │  │
│         │             └─────────────────┼───────────────────┘  │
│         │                               │                      │
│         └───────────────┬───────────────┘                      │
│                         ▼                                      │
│               ┌───────────────────┐                            │
│               │  Eigent Backend   │                            │
│               │  (FastAPI + DB)   │                            │
│               └───────────────────┘                            │
└─────────────────────────────────────────────────────────────────┘
                          │
                          ▼ (external)
                ┌───────────────────┐
                │   Deepgram API    │
                └───────────────────┘
```

### Key Connections

- **Electron ↔ Voice Service:** Audio streams (mic in, speaker out) over local WebSocket
- **Voice Service ↔ Deepgram:** Real-time voice agent connection (STT + LLM + TTS)
- **Voice Service ↔ Eigent Backend:** REST API for prompts, SSE for progress events
- **Electron ↔ Eigent Backend:** Existing connection unchanged

The voice service holds the user's auth token (passed from Electron) and acts on their behalf.

---

## Technology

**Deepgram Voice Agent API** provides the full voice stack:
- Speech-to-text (Nova-3)
- LLM orchestration (configurable: GPT-4o, Claude, etc.)
- Text-to-speech (Aura-2)
- Built-in turn-taking, barge-in detection, conversation flow

### What Deepgram Handles

- Turn-taking and conversation flow
- Barge-in detection (user interrupts agent)
- Audio streaming (STT ↔ TTS pipeline)
- LLM orchestration (sends to LLM, streams response)
- Keep-alive and connection management

### What We Configure

- **System prompt** - Agent personality and behavior
- **LLM model** - GPT-4o, Claude, etc.
- **Voice** - TTS model selection
- **Functions** - Tools the agent can call

### What We Implement

Function handlers that connect to Eigent:
- `submit_task(prompt)` - Send refined prompt to Eigent backend
- `get_project_context()` - Fetch current files, recent tasks
- `get_task_status()` - Check workforce progress
- `confirm_start()` - User confirms task execution

---

## Function Definitions

### Task Lifecycle

| Function | Purpose | When agent calls it |
|----------|---------|---------------------|
| `submit_task(prompt)` | Send refined prompt to Eigent | After clarifications complete, ready to execute |
| `confirm_start()` | User confirms task decomposition | Eigent has planned subtasks, awaiting go-ahead |
| `cancel_task()` | Abort current execution | User says "stop" or "cancel" |
| `pause_task()` / `resume_task()` | Pause/resume workforce | User needs a break |

### Context & Status

| Function | Purpose | When agent calls it |
|----------|---------|---------------------|
| `get_project_context()` | Fetch files, recent history | Agent needs to formulate a specific prompt |
| `get_task_status()` | Current progress (3/5 done, etc.) | User asks "how's it going?" |
| `get_task_result()` | Final output summary | Task complete, agent summarizes for voice |

### Conversation Flow

| Function | Purpose | When agent calls it |
|----------|---------|---------------------|
| `notify_user(message)` | Push a voice notification | SSE event triggers milestone announcement |

---

## SSE Events → Voice Notifications

The voice service subscribes to Eigent's SSE stream and decides what to announce.

### Events That Trigger Voice

| SSE Event | Voice Response |
|-----------|----------------|
| `decompose_progress` | "I've broken this into 4 tasks. Ready to start?" |
| `task_state: completed` (subtask) | "2 of 4 done." |
| `task_state: completed` (all) | "All done. [brief summary of result]" |
| `task_state: failed` | "Task 3 failed - should I retry or skip it?" |
| `timeout` | "This is taking a while. Keep waiting or cancel?" |

### Events That Stay Silent

| SSE Event | Why silent |
|-----------|------------|
| `activate_agent` / `deactivate_agent` | Routine, not a milestone |
| `activate_toolkit` / `deactivate_toolkit` | Too granular |
| `assign_task` (state: waiting/running) | Progress, not a decision point |

---

## Voice UI

A minimal floating panel shows the voice conversation transcript.

### Default State (Collapsed)

```
┌─────────────────────────────┐
│ 🎙️ Voice Active        ⬜ ✕ │
│ "Deploy to staging"         │
│ "Got it, running tests..."  │
└─────────────────────────────┘
```

- Small, draggable overlay (bottom-right corner default)
- Shows last 2-3 exchanges
- Expand button pops out to separate window
- Close button deactivates voice mode

### Expanded State (Separate Window)

```
┌────────────────────────────────────┐
│ 🎙️ Voice Conversation         ─ ✕ │
├────────────────────────────────────┤
│ You: "make the button blue"        │
│ Agent: "Which button - save or     │
│         cancel?"                   │
│ You: "save"                        │
│ Agent: "Got it, updating save      │
│         button to blue."           │
│                                    │
│ ─────── Task Submitted ───────     │
│                                    │
│ Agent: "Done. The button is now    │
│         blue."                     │
├────────────────────────────────────┤
│ [Mute] [Pause] [End Session]       │
└────────────────────────────────────┘
```

- Full transcript with timestamps
- Visual separator when tasks are submitted to Eigent
- Controls for mute (mic off), pause (stops listening), end session

### Eigent UI Integration

When a task is submitted, the refined prompt appears in the existing chat panel as if the user typed it. The main UI continues to show workflow execution, agent nodes, and full results.

---

## Session Lifecycle

### Activation

User activates voice mode via:
- Keyboard shortcut (e.g., `Cmd+Shift+V`)
- Button in the UI (microphone icon)
- Future: wake word ("Hey Eigent")

On activation:
1. Electron starts audio capture, streams to voice service
2. Voice service connects to Deepgram WebSocket
3. Voice service fetches project context from Eigent
4. Voice service subscribes to Eigent SSE
5. Floating panel appears
6. Agent speaks greeting: "Ready. What would you like to do?"

### During Session

- Always listening (no push-to-talk needed)
- User can speak anytime, agent handles turn-taking
- Session persists across multiple tasks
- Context accumulates (agent remembers earlier conversation)

### Deactivation

User ends session via:
- Close button on panel
- Keyboard shortcut
- Saying "end session" or "goodbye"

On deactivation:
1. Agent speaks: "Session ended."
2. Deepgram WebSocket closes
3. SSE subscription closes
4. Audio capture stops
5. Panel disappears

---

## Voice Agent System Prompt

```
You are the voice interface for Eigent, a multi-agent AI workforce.

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
```

---

## Implementation Phases

### Phase 1: Voice Service Foundation
- Python service with Deepgram WebSocket connection
- Basic function handlers (submit_task, get_task_status)
- Connect to Eigent REST API
- Local audio streaming from Electron via WebSocket

### Phase 2: SSE Integration
- Subscribe to Eigent's event stream
- Event categorization (voice-worthy vs silent)
- Notification triggering via Deepgram

### Phase 3: Project Context
- Fetch context at session start
- Subscribe to project changes
- Inject context into function responses

### Phase 4: Electron Integration
- Audio capture/playback in Electron main process
- Floating voice panel UI (React component)
- Pop-out window functionality
- Activation keyboard shortcut

### Phase 5: Polish
- Refine system prompt based on real usage
- Tune which events trigger voice
- Add mute/pause controls
- Error handling and reconnection

---

## Design Decisions Summary

| Aspect | Decision |
|--------|----------|
| **Architecture** | Separate Python voice service (local sidecar) |
| **Voice tech** | Deepgram Voice Agent API (STT + LLM + TTS) |
| **Role** | Intelligent user proxy - clarifies, formulates prompts, summarizes |
| **Activation** | Always listening when active, keyboard shortcut to start |
| **Notifications** | Milestone-based (not chatty, not silent) |
| **Confirmations** | Critical decisions voiced, routine progress auto-proceeds |
| **Context** | Project-aware (sync at start + continuous updates) |
| **UI** | Floating transcript panel, can pop out to separate window |
| **Eigent changes** | None - voice service is just another API client |
