"""
Backup Plugin v2.1 — Automated database backups to Google Drive.
Scheduled backups with retention policies.

v2.1 CHANGES:
  - Added event bus integration for backup_completed events
  - Defensive event publishing with fallback handling
  - Enhanced event payload with backup metadata

v2.0 UPGRADES:
  - Notification on backup failure
  - Safe folder name escaping for Google Drive queries
  - Proper pg_dump connection string handling
  - Health check & statistics
  - FIXED: Connection string resolution
  - FIXED: Retry logic for Google Drive uploads
  - FIXED: Backup size validation
"""

import os
import json
import logging
import threading
import time
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

logger = logging.getLogger("kanyoza.plugins.backup")


# ═══════════════════════════════════════════════════════════════════════════
# EVENT BUS INTEGRATION
# ═══════════════════════════════════════════════════════════════════════════

try:
    from core.shared_event_bus import get_shared_event_bus
    HAS_EVENT_BUS = True
except ImportError:
    HAS_EVENT_BUS = False
    logger.warning("Shared event bus not available — events will be skipped")


def _publish_event(event_name: str, payload: Dict[str, Any]) -> None:
    """Safely publish event to shared bus with defensive error handling."""
    if not HAS_EVENT_BUS:
        return
    try:
        bus = get_shared_event_bus()
        bus.publish(event_name, payload)
    except Exception:
        # Never let event publishing break core logic
        pass


# ═══════════════════════════════════════════════════════════════════════════
# GOOGLE DRIVE HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _get_drive_service():
    """Get Google Drive service from environment credentials."""
    creds_json = os.getenv("GDRIVE_SERVICE_ACCOUNT", "{}")
    creds_dict = json.loads(creds_json)
    credentials = (
        service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=["https://www.googleapis.com/auth/drive.file"],
        )
    )
    return build("drive", "v3", credentials=credentials)


def _escape_drive_query(value: str) -> str:
    """Escape a value for use in Google Drive query strings.

    Replaces single quotes with \' to prevent query injection.
    """
    return value.replace("'", "\\'")


# ═══════════════════════════════════════════════════════════════════════════
# BACKUP PLUGIN v2.1
# ═══════════════════════════════════════════════════════════════════════════

class BackupPlugin:
    """Automated database backup service with Google Drive storage."""

    def __init__(
        self,
        supabase_client=None,
        redis_client=None,
    ):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "backup"
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._backups_completed = 0
        self._backups_failed = 0
        self._last_backup_at: Optional[datetime] = None

    # ═══════════════════════════════════════════════════════════════════
    # PLUGIN INTERFACE
    # ═══════════════════════════════════════════════════════════════════

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "backup_status",
                "description": "Get backup status",
                "handler": self._handle_status,
            },
            {
                "name": "backup_run",
                "description": "Run backup now",
                "handler": self._handle_run,
            },
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return []

    # ═══════════════════════════════════════════════════════════════════
    # LIFECYCLE
    # ═══════════════════════════════════════════════════════════════════

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            daemon=True,
            name="backup-plugin",
        )
        self._thread.start()
        logger.info("BackupPlugin: started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("BackupPlugin: stopped")

    def shutdown(self):
        """Alternative stop method for compatibility."""
        self.stop()

    # ═══════════════════════════════════════════════════════════════════
    # MAIN LOOP
    # ═══════════════════════════════════════════════════════════════════

    def _loop(self):
        while self._running:
            try:
                self._process_scheduled_backups()
            except Exception as e:
                logger.error(
                    "BackupPlugin error: %s", e
                )
            time.sleep(300)  # Check every 5 minutes

    def _process_scheduled_backups(self):
        if not self.supabase:
            return

        targets = (
            self.supabase.table("backup_targets")
            .select("*")
            .eq("enabled", True)
            .execute()
        )
        now = datetime.now(timezone.utc)

        for target in targets.data or []:
            if self._should_run(target, now):
                self._run_backup(target)

    def _should_run(
        self, target: dict, now: datetime
    ) -> bool:
        last = (
            self.supabase.table("backup_records")
            .select("started_at")
            .eq("target_id", target["id"])
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        if not last.data:
            return True

        try:
            last_time = datetime.fromisoformat(
                last.data[0]["started_at"].replace(
                    "Z", "+00:00"
                )
            )
            hours_since = (
                now - last_time
            ).total_seconds() / 3600
            return hours_since >= 24
        except Exception:
            return True

    # ═══════════════════════════════════════════════════════════════════
    # BACKUP EXECUTION
    # ═══════════════════════════════════════════════════════════════════

    def _run_backup(self, target: dict):
        record_id = None
        tmp_path = None

        try:
            filename = (
                f"backup_{target['name']}_"
                f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sql"
            )

            # Create backup record
            result = (
                self.supabase.table("backup_records")
                .insert({
                    "target_id": target["id"],
                    "filename": filename,
                    "status": "running",
                    "started_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                })
                .execute()
            )
            record_id = (
                result.data[0]["id"]
                if result.data
                else None
            )

            # ─── Get connection string ──────────────────────────────────
            conn_str = target.get("connection_string")
            if not conn_str:
                conn_str = target.get("connection_string_encrypted")
            if not conn_str:
                raise ValueError("No connection string found for backup target")

            # Dump database
            logger.info(
                "BackupPlugin: dumping %s",
                target["name"],
            )
            tmp_path = f"/tmp/{filename}"

            # Check if pg_dump is available
            try:
                subprocess.run(
                    ["pg_dump", "--version"],
                    check=True,
                    capture_output=True,
                    timeout=10,
                )
            except (subprocess.CalledProcessError, FileNotFoundError):
                logger.error("pg_dump not found. Please install postgresql-client.")
                raise RuntimeError("pg_dump not available")

            result = subprocess.run(
                [
                    "pg_dump",
                    "--dbname",
                    conn_str,
                    "-f",
                    tmp_path,
                    "--no-owner",
                    "--no-acl",
                    "--clean",
                ],
                check=False,  # Don't raise immediately, we want to capture stderr
                capture_output=True,
                timeout=300,
            )

            if result.returncode != 0:
                error_msg = result.stderr.decode('utf-8', errors='ignore')[:500]
                raise RuntimeError(f"pg_dump failed: {error_msg}")

            # ─── Validate backup size ──────────────────────────────────
            if not os.path.exists(tmp_path):
                raise RuntimeError("Backup file was not created")

            file_size = os.path.getsize(tmp_path)
            if file_size < 1024:  # Less than 1KB
                logger.warning(
                    "BackupPlugin: backup file is unusually small (%d bytes)",
                    file_size
                )
                # Still upload, but log a warning

            # Upload to Google Drive with retry
            logger.info(
                "BackupPlugin: uploading %s to Google Drive "
                "(%d bytes)",
                filename,
                file_size,
            )
            drive_file = self._upload_to_drive_with_retry(tmp_path, filename)

            # Mark completed
            self.supabase.table(
                "backup_records"
            ).update({
                "status": "completed",
                "size_bytes": file_size,
                "storage_url": drive_file.get(
                    "webViewLink",
                    f"drive://{drive_file['id']}",
                ),
                "completed_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            }).eq("id", record_id).execute()

            self._cleanup_old_backups(target)

            self._backups_completed += 1
            self._last_backup_at = datetime.now(
                timezone.utc
            )

            logger.info(
                "BackupPlugin: %s backup complete — "
                "%d bytes",
                target["name"],
                file_size,
            )

            # Publish backup_completed event
            _publish_event("backup_completed", {
                "status": "completed",
                "target_id": target.get("id", ""),
                "target_name": target.get("name", ""),
                "filename": filename,
                "size_bytes": file_size,
                "storage_url": drive_file.get(
                    "webViewLink",
                    f"drive://{drive_file['id']}",
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # Optional: notify success
            if os.getenv("BACKUP_NOTIFY_SUCCESS", "false").lower() == "true":
                self._notify_success(target, file_size)

        except Exception as e:
            logger.error(
                "BackupPlugin: %s failed: %s",
                target["name"],
                e,
            )
            self._backups_failed += 1

            if record_id:
                self.supabase.table(
                    "backup_records"
                ).update({
                    "status": "failed",
                    "error_message": str(e)[:500],
                    "completed_at": datetime.now(
                        timezone.utc
                    ).isoformat(),
                }).eq("id", record_id).execute()

            # Publish backup_completed event with failure status
            _publish_event("backup_completed", {
                "status": "failed",
                "target_id": target.get("id", ""),
                "target_name": target.get("name", ""),
                "error": str(e)[:500],
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            # v2.0 — Notify on failure
            self._notify_failure(target, str(e))

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    def _upload_to_drive_with_retry(
        self,
        file_path: str,
        filename: str,
        max_retries: int = 3
    ) -> dict:
        """Upload to Google Drive with exponential backoff retry."""
        service = _get_drive_service()
        folder_name = os.getenv(
            "GDRIVE_BACKUP_FOLDER",
            "Kanyoza-Backups",
        )
        folder_id = self._get_or_create_folder(
            service, folder_name
        )

        for attempt in range(max_retries):
            try:
                media = MediaFileUpload(
                    file_path,
                    mimetype="application/sql",
                    resumable=True,
                )
                drive_file = (
                    service.files()
                    .create(
                        body={
                            "name": filename,
                            "parents": [folder_id],
                        },
                        media_body=media,
                        fields="id,webViewLink",
                    )
                    .execute()
                )
                return drive_file
            except HttpError as e:
                if e.resp.status in (429, 500, 502, 503, 504) and attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1
                    logger.warning(
                        "BackupPlugin: upload retry %d/%d in %.2fs: %s",
                        attempt + 1, max_retries, wait_time, e
                    )
                    time.sleep(wait_time)
                    continue
                raise
            except Exception as e:
                if attempt < max_retries - 1:
                    wait_time = (2 ** attempt) + 1
                    logger.warning(
                        "BackupPlugin: upload retry %d/%d in %.2fs: %s",
                        attempt + 1, max_retries, wait_time, e
                    )
                    time.sleep(wait_time)
                    continue
                raise
        raise RuntimeError(f"Failed to upload {filename} after {max_retries} attempts")

    # ═══════════════════════════════════════════════════════════════════
    # GOOGLE DRIVE HELPERS
    # ═══════════════════════════════════════════════════════════════════

    def _get_or_create_folder(
        self, service, folder_name: str
    ) -> str:
        """Get or create a folder in Google Drive.

        Uses escaped query to prevent injection from folder names.
        """
        escaped = _escape_drive_query(folder_name)
        response = (
            service.files()
            .list(
                q=(
                    f"name='{escaped}' and "
                    f"mimeType='application/vnd.google-apps.folder' "
                    f"and trashed=false"
                ),
                spaces="drive",
                fields="files(id, name)",
            )
            .execute()
        )
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        folder = (
            service.files()
            .create(
                body={
                    "name": folder_name,
                    "mimeType": (
                        "application/vnd.google-apps.folder"
                    ),
                },
                fields="id",
            )
            .execute()
        )
        return folder["id"]

    def _cleanup_old_backups(self, target: dict):
        """Delete backups older than retention period."""
        retention_days = target.get("retention_days", 30)
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)

        try:
            old_records = (
                self.supabase.table("backup_records")
                .select("id,filename")
                .eq("target_id", target["id"])
                .eq("status", "completed")
                .lt(
                    "completed_at", cutoff.isoformat()
                )
                .execute()
            )

            service = _get_drive_service()
            folder_name = os.getenv(
                "GDRIVE_BACKUP_FOLDER",
                "Kanyoza-Backups",
            )
            folder_id = self._get_or_create_folder(
                service, folder_name
            )

            for record in old_records.data or []:
                try:
                    filename = record["filename"]
                    escaped = _escape_drive_query(filename)
                    response = (
                        service.files()
                        .list(
                            q=(
                                f"name='{escaped}' and "
                                f"'{folder_id}' in parents "
                                f"and trashed=false"
                            ),
                            fields="files(id)",
                        )
                        .execute()
                    )
                    for f in response.get("files", []):
                        service.files().delete(
                            fileId=f["id"]
                        ).execute()
                        logger.info(
                            "BackupPlugin: deleted old backup "
                            "%s from Drive",
                            filename,
                        )
                except Exception as e:
                    logger.debug(
                        "BackupPlugin: cleanup Drive error: %s",
                        e,
                    )

                self.supabase.table(
                    "backup_records"
                ).delete().eq(
                    "id", record["id"]
                ).execute()

            logger.info(
                "BackupPlugin: cleaned up %d old backups for %s",
                len(old_records.data or []),
                target["name"]
            )

        except Exception as e:
            logger.error("BackupPlugin: cleanup failed: %s", e)

    # ═══════════════════════════════════════════════════════════════════
    # v2.0 — NOTIFICATIONS
    # ═══════════════════════════════════════════════════════════════════

    def _notify_failure(
        self, target: dict, error: str
    ) -> None:
        """Send notification on backup failure."""
        try:
            from services.email_service import (
                email_service,
            )

            email_service.send_telegram(
                f"🔴 <b>Backup Failed: {target['name']}</b>\n"
                f"{error[:300]}\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug(
                "Backup failure notification failed: %s",
                exc,
            )

    def _notify_success(
        self, target: dict, size_bytes: int
    ) -> None:
        """Send success notification."""
        try:
            from services.email_service import (
                email_service,
            )

            size_mb = size_bytes / (1024 * 1024)
            email_service.send_telegram(
                f"🟢 <b>Backup Successful: {target['name']}</b>\n"
                f"Size: {size_mb:.2f} MB\n"
                f"<i>{datetime.now(timezone.utc).strftime('%H:%M UTC')}</i>"
            )
        except Exception as exc:
            logger.debug(
                "Backup success notification failed: %s",
                exc,
            )

    # ═══════════════════════════════════════════════════════════════════
    # STATISTICS
    # ═══════════════════════════════════════════════════════════════════

    def get_stats(self) -> dict:
        """Return backup statistics."""
        return {
            "backups_completed": self._backups_completed,
            "backups_failed": self._backups_failed,
            "last_backup_at": (
                self._last_backup_at.isoformat()
                if self._last_backup_at
                else None
            ),
            "running": self._running,
        }

    def health_check(self) -> dict:
        """Quick health check."""
        stats = self.get_stats()
        status = "healthy"

        if not self._running:
            status = "stopped"
        elif (
            self._backups_failed > 0
            and self._backups_completed == 0
        ):
            status = "degraded"

        return {
            "status": status,
            "stats": stats,
        }

    # ═══════════════════════════════════════════════════════════════════
    # COMMAND HANDLERS
    # ═══════════════════════════════════════════════════════════════════

    def _handle_status(
        self, params: dict
    ) -> dict:
        if not self.supabase:
            return {
                "ok": False,
                "error": "Database not available",
            }
        targets = (
            self.supabase.table("backup_targets")
            .select("*")
            .execute()
        )
        stats = self.get_stats()
        return {
            "ok": True,
            "targets": targets.data or [],
            "stats": stats,
        }

    def _handle_run(self, params: dict) -> dict:
        if not self.supabase:
            return {
                "ok": False,
                "error": "Database not available",
            }
        targets = (
            self.supabase.table("backup_targets")
            .select("*")
            .eq("enabled", True)
            .execute()
        )
        for target in targets.data or []:
            self._run_backup(target)
        return {
            "ok": True,
            "message": (
                f"Backup triggered for "
                f"{len(targets.data or [])} targets"
            ),
        }


# ═══════════════════════════════════════════════════════════════════════════
# MODULE SINGLETON
# ═══════════════════════════════════════════════════════════════════════════

backup_plugin: Optional[BackupPlugin] = None


def init_backup_plugin(
    supabase_client=None, redis_client=None
) -> BackupPlugin:
    global backup_plugin
    if backup_plugin is None:
        backup_plugin = BackupPlugin(
            supabase_client, redis_client
        )
        backup_plugin.start()
    return backup_plugin
