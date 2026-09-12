"""
Kanyoza Systems AI Platform v10 — Shared Domain Models
All services exchange these typed models. Never pass raw dicts between services.
"""

from models.content import TechPost, PostTheme, PostFormat
from models.conversation import Conversation, ConversationTurn, MemoryProfile, UserProfile
from models.guardian import RepositoryIssue, GuardianReport, IssueSeverity, IssueCategory
from models.scheduler import ScheduledJob, JobStatus as SchedulerJobStatus, JobType
from models.automation_job import AutomationJob, JobStatus  # Phase 2: pipeline job status
from models.notification import Notification, NotificationChannel, NotificationPriority

__all__ = [
    # Content
    "TechPost",
    "PostTheme",
    "PostFormat",
    # Conversation / Memory
    "Conversation",
    "ConversationTurn",
    "MemoryProfile",
    "UserProfile",
    # Guardian
    "RepositoryIssue",
    "GuardianReport",
    "IssueSeverity",
    "IssueCategory",
    # Scheduler
    "ScheduledJob",
    "SchedulerJobStatus",
    "JobType",
    # Automation jobs (content pipeline)
    "AutomationJob",
    "JobStatus",
    # Notification
    "Notification",
    "NotificationChannel",
    "NotificationPriority",
]
