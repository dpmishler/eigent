"""Client for interacting with Eigent backend."""

import logging
from typing import AsyncGenerator, Optional

import httpx
from httpx_sse import aconnect_sse

from app.config import settings
from app.models import ProjectContext, TaskStatus, SSEEvent

logger = logging.getLogger(__name__)


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
        logger.debug("EigentClient connected to %s", self.base_url)
        return self

    async def __aexit__(self, *args):
        if self._client:
            await self._client.aclose()
            logger.debug("EigentClient connection closed")

    async def submit_task(self, project_id: str, prompt: str) -> str:
        """Submit a task to Eigent and return task ID.

        Args:
            project_id: The project identifier.
            prompt: The task prompt to submit.

        Returns:
            The task ID assigned by the backend.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        logger.info("Submitting task for project %s", project_id)
        logger.debug("Task prompt: %s", prompt[:100] if len(prompt) > 100 else prompt)

        try:
            response = await self._client.post(
                "/chat",
                json={
                    "project_id": project_id,
                    "question": prompt,
                },
            )
            response.raise_for_status()
            task_id = response.json().get("task_id")
            logger.info("Task submitted successfully, task_id=%s", task_id)
            return task_id
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to submit task: HTTP %s - %s",
                e.response.status_code,
                e.response.text,
            )
            raise
        except httpx.RequestError as e:
            logger.error("Request error while submitting task: %s", e)
            raise

    async def confirm_start(self, project_id: str) -> bool:
        """Confirm task decomposition and start execution.

        Args:
            project_id: The project identifier.

        Returns:
            True if confirmation was successful.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        logger.info("Confirming task start for project %s", project_id)

        try:
            response = await self._client.post(
                f"/chat/{project_id}/confirm",
            )
            response.raise_for_status()
            logger.info("Task start confirmed for project %s", project_id)
            return True
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to confirm task start: HTTP %s - %s",
                e.response.status_code,
                e.response.text,
            )
            raise
        except httpx.RequestError as e:
            logger.error("Request error while confirming task: %s", e)
            raise

    async def cancel_task(self, project_id: str) -> bool:
        """Cancel the current task.

        Args:
            project_id: The project identifier.

        Returns:
            True if cancellation was successful.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        logger.info("Cancelling task for project %s", project_id)

        try:
            response = await self._client.post(
                f"/chat/{project_id}/cancel",
            )
            response.raise_for_status()
            logger.info("Task cancelled for project %s", project_id)
            return True
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to cancel task: HTTP %s - %s",
                e.response.status_code,
                e.response.text,
            )
            raise
        except httpx.RequestError as e:
            logger.error("Request error while cancelling task: %s", e)
            raise

    async def get_project_context(self, project_id: str) -> ProjectContext:
        """Get project files and recent task history.

        Args:
            project_id: The project identifier.

        Returns:
            ProjectContext with files and recent tasks.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        logger.debug("Getting project context for %s", project_id)

        try:
            response = await self._client.get(f"/project/{project_id}/context")
            response.raise_for_status()
            context = ProjectContext(**response.json())
            logger.debug(
                "Retrieved project context: %d files, %d recent tasks",
                len(context.files),
                len(context.recent_tasks),
            )
            return context
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to get project context: HTTP %s - %s",
                e.response.status_code,
                e.response.text,
            )
            raise
        except httpx.RequestError as e:
            logger.error("Request error while getting project context: %s", e)
            raise

    async def get_task_status(self, project_id: str) -> TaskStatus:
        """Get current task execution status.

        Args:
            project_id: The project identifier.

        Returns:
            TaskStatus with current execution status.

        Raises:
            httpx.HTTPStatusError: If the request fails.
        """
        logger.debug("Getting task status for project %s", project_id)

        try:
            response = await self._client.get(f"/chat/{project_id}/status")
            response.raise_for_status()
            status = TaskStatus(**response.json())
            logger.debug(
                "Task status: %d/%d completed, %d running, %d failed",
                status.completed,
                status.total,
                status.running,
                status.failed,
            )
            return status
        except httpx.HTTPStatusError as e:
            logger.error(
                "Failed to get task status: HTTP %s - %s",
                e.response.status_code,
                e.response.text,
            )
            raise
        except httpx.RequestError as e:
            logger.error("Request error while getting task status: %s", e)
            raise

    async def subscribe_events(
        self, project_id: str
    ) -> AsyncGenerator[SSEEvent, None]:
        """Subscribe to SSE events for a project.

        Args:
            project_id: The project identifier.

        Yields:
            SSEEvent objects as they are received.

        Raises:
            httpx.HTTPStatusError: If the connection fails.
        """
        logger.info("Subscribing to SSE events for project %s", project_id)

        try:
            async with aconnect_sse(
                self._client,
                "GET",
                f"/chat/{project_id}/events",
            ) as event_source:
                logger.debug("SSE connection established for project %s", project_id)
                async for event in event_source.aiter_sse():
                    logger.debug("Received SSE event: %s", event.event)
                    yield SSEEvent(
                        event=event.event,
                        data=event.json() if event.data else {},
                    )
        except httpx.HTTPStatusError as e:
            logger.error(
                "SSE connection failed: HTTP %s - %s",
                e.response.status_code,
                e.response.text,
            )
            raise
        except httpx.RequestError as e:
            logger.error("Request error during SSE subscription: %s", e)
            raise
        finally:
            logger.debug("SSE subscription ended for project %s", project_id)
