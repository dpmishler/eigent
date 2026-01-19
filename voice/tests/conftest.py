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
