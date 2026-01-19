"""Data models for Eigent voice service."""

from typing import Optional, List
from pydantic import BaseModel
from enum import Enum


class TaskState(str, Enum):
    """Possible states for a task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskInfo(BaseModel):
    """Information about a task."""

    id: str
    content: str
    state: TaskState
    result: Optional[str] = None


class ProjectContext(BaseModel):
    """Project context including files and recent tasks."""

    project_id: str
    files: List[str]
    recent_tasks: List[TaskInfo]


class TaskStatus(BaseModel):
    """Current task execution status."""

    total: int
    completed: int
    running: int
    failed: int
    current_task: Optional[str] = None


class SSEEvent(BaseModel):
    """Server-Sent Event data."""

    event: str
    data: dict
