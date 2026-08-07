# plugins/backup/plugin.py
"""
Backup Plugin — Automated database backups to Google Drive.
Scheduled backups with retention policies.
"""

import os
import json
import logging
import threading
import time
import tempfile
import subprocess
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger("kanyoza.plugins.backup")


def _get_drive_service():
    """Get Google Drive service from environment credentials."""
    creds_json = os.getenv("GDRIVE_SERVICE_ACCOUNT", "{}")
    creds_dict = json.loads(creds_json)
    credentials = service_account.Credentials.from_service_account_info(
        creds_dict, scopes=["https://www.googleapis.com/auth/drive.file"]
    )
    return build("drive", "v3", credentials=credentials)


class BackupPlugin:
    """Automated database backup service with Google Drive storage."""

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client
        self.name = "backup"
        self._running = False
        self._thread = None

    def get_commands(self) -> List[Dict[str, Any]]:
        return [
            {"name": "backup_status", "description": "Get backup status", "handler": self._handle_status},
            {"name": "backup_run", "description": "Run backup now", "handler": self._handle_run},
        ]

    def get_webhooks(self) -> List[Dict[str, Any]]:
        return []

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="backup-plugin")
        self._thread.start()
        logger.info("BackupPlugin: started")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("BackupPlugin: stopped")

    def _loop(self):
        while self._running:
            try:
                self._process_scheduled_backups()
            except Exception as e:
                logger.error("BackupPlugin error: %s", e)
            time.sleep(300)

    def _process_scheduled_backups(self):
        if not self.supabase:
            return
        targets = self.supabase.table("backup_targets").select("*").eq("enabled", True).execute()
        now = datetime.now(timezone.utc)
        for target in (targets.data or []):
            if self._should_run(target, now):
                self._run_backup(target)

    def _should_run(self, target: dict, now: datetime) -> bool:
        last = self.supabase.table("backup_records") \
            .select("started_at").eq("target_id", target["id"]) \
            .order("started_at", desc=True).limit(1).execute()
        if not last.data:
            return True
        last_time = datetime.fromisoformat(last.data[0]["started_at"].replace("Z", "+00:00"))
        hours_since = (now - last_time).total_seconds() / 3600
        return hours_since >= 24

    def _run_backup(self, target: dict):
        record_id = None
        tmp_path = None
        try:
            filename = f"backup_{target['name']}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.sql"
            
            result = self.supabase.table("backup_records").insert({
                "target_id": target["id"],
                "filename": filename,
                "status": "running",
                "started_at": datetime.now(timezone.utc).isoformat(),
            }).execute()
            record_id = result.data[0]["id"] if result.data else None

            logger.info("BackupPlugin: dumping %s", target["name"])
            conn_str = target["connection_string_encrypted"]
            tmp_path = f"/tmp/{filename}"
            subprocess.run(
                ["pg_dump", conn_str, "-f", tmp_path, "--no-owner", "--no-acl"],
                check=True, capture_output=True, timeout=300
            )
            file_size = os.path.getsize(tmp_path)

            logger.info("BackupPlugin: uploading %s to Google Drive (%d bytes)", filename, file_size)
            service = _get_drive_service()
            folder_name = os.getenv("GDRIVE_BACKUP_FOLDER", "Kanyoza-Backups")
            folder_id = self._get_or_create_folder(service, folder_name)
            
            media = MediaFileUpload(tmp_path, mimetype="application/sql", resumable=True)
            drive_file = service.files().create(
                body={"name": filename, "parents": [folder_id]},
                media_body=media,
                fields="id,webViewLink"
            ).execute()

            self.supabase.table("backup_records").update({
                "status": "completed",
                "size_bytes": file_size,
                "storage_url": drive_file.get("webViewLink", f"drive://{drive_file['id']}"),
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", record_id).execute()

            self._cleanup_old_backups(target, service, folder_id)
            logger.info("BackupPlugin: %s backup complete — %d bytes", target["name"], file_size)

        except Exception as e:
            logger.error("BackupPlugin: %s failed: %s", target["name"], e)
            if record_id:
                self.supabase.table("backup_records").update({
                    "status": "failed",
                    "error_message": str(e)[:500],
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", record_id).execute()
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def _get_or_create_folder(self, service, folder_name: str) -> str:
        response = service.files().list(
            q=f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            spaces="drive", fields="files(id, name)"
        ).execute()
        files = response.get("files", [])
        if files:
            return files[0]["id"]
        folder = service.files().create(
            body={"name": folder_name, "mimeType": "application/vnd.google-apps.folder"},
            fields="id"
        ).execute()
        return folder["id"]

    def _cleanup_old_backups(self, target: dict, service, folder_id: str):
        cutoff = datetime.now(timezone.utc) - timedelta(days=target.get("retention_days", 30))
        old_records = self.supabase.table("backup_records") \
            .select("id,filename").eq("target_id", target["id"]) \
            .eq("status", "completed").lt("completed_at", cutoff.isoformat()).execute()
        for record in (old_records.data or []):
            try:
                response = service.files().list(
                    q=f"name='{record['filename']}' and '{folder_id}' in parents and trashed=false",
                    fields="files(id)"
                ).execute()
                for f in response.get("files", []):
                    service.files().delete(fileId=f["id"]).execute()
                    logger.info("BackupPlugin: deleted old backup %s from Drive", record["filename"])
            except Exception as e:
                logger.debug("BackupPlugin: cleanup Drive error: %s", e)
            self.supabase.table("backup_records").delete().eq("id", record["id"]).execute()

    def _handle_status(self, params: dict) -> dict:
        if not self.supabase:
            return {"ok": False, "error": "Database not available"}
        targets = self.supabase.table("backup_targets").select("*").execute()
        return {"ok": True, "targets": targets.data or []}

    def _handle_run(self, params: dict) -> dict:
        if not self.supabase:
            return {"ok": False, "error": "Database not available"}
        targets = self.supabase.table("backup_targets").select("*").eq("enabled", True).execute()
        for target in (targets.data or []):
            self._run_backup(target)
        return {"ok": True, "message": f"Backup triggered for {len(targets.data or [])} targets"}


backup_plugin: BackupPlugin = None


def init_backup_plugin(supabase_client=None, redis_client=None) -> BackupPlugin:
    global backup_plugin
    if backup_plugin is None:
        backup_plugin = BackupPlugin(supabase_client, redis_client)
        backup_plugin.start()
    return backup_plugin
