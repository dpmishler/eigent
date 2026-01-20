import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from app.session import VoiceSession


@pytest.mark.asyncio
async def test_session_start_and_stop(mock_deepgram, mock_eigent_client):
    with patch('app.session.VoiceAgent') as MockAgent, \
         patch('app.session.EigentClient') as MockClient:

        MockClient.return_value.__aenter__ = AsyncMock(return_value=mock_eigent_client)
        MockClient.return_value.__aexit__ = AsyncMock()

        mock_agent = MagicMock()
        mock_agent.connect = AsyncMock()
        mock_agent.disconnect = AsyncMock()
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

        # Note: submit_task now notifies frontend instead of directly submitting
        # Backend integration requires API keys from frontend
        assert result["status"] == "prompt_ready"
        assert "message" in result
        assert submitted_prompt == "Test task"
