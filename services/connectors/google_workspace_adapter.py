"""Governed Google Workspace connector boundary.

The adapter exposes a small, explicit set of high-value Workspace capabilities
through the same receipt protocol used by the other enterprise providers.
Credentials are supplied by the caller; the Action Kernel remains the sole
execution authority.
"""
from __future__ import annotations

from typing import Any, Optional
from urllib.parse import quote

from services.connectors.enterprise_provider_base import EnterpriseProviderAdapter, ProviderAdapterError


class GoogleWorkspaceAdapter(EnterpriseProviderAdapter):
    provider = "google_workspace"
    DEFAULT_BASE_URL = "https://www.googleapis.com"

    def __init__(self, *, token: str, base_url: str = DEFAULT_BASE_URL, **kwargs: Any):
        super().__init__(token=token, base_url=base_url, **kwargs)

    def _workspace_request(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any, Any]:
        return self._request(method, path, **kwargs)

    def get_drive_file(self, file_id: str, *, fields: str = "id,name,mimeType,webViewLink,modifiedTime") -> dict[str, Any]:
        try:
            _, payload, headers = self._workspace_request(
                "GET", f"/drive/v3/files/{quote(str(file_id), safe='')}",
                params={"fields": fields},
            )
            return self._receipt(status="succeeded", object_id=payload.get("id") if isinstance(payload, dict) else file_id,
                                 request_id=headers.get("X-GUploader-UploadID"), metadata={"file": payload})
        except ProviderAdapterError as exc:
            return self._handle_mutation_error(exc)

    def search_drive_files(self, query: str, *, page_size: int = 100) -> dict[str, Any]:
        try:
            _, payload, headers = self._workspace_request(
                "GET", "/drive/v3/files", params={"q": query, "pageSize": min(int(page_size), 1000), "fields": "files(id,name,mimeType,webViewLink,modifiedTime),nextPageToken"},
            )
            return self._receipt(status="succeeded", object_id=None, request_id=headers.get("X-Request-Id"), metadata={"files": payload})
        except ProviderAdapterError as exc:
            return self._handle_mutation_error(exc)

    def send_gmail_message(self, *, raw_message: str, user_id: str = "me", confirm: bool = False) -> dict[str, Any]:
        try:
            self._require_mutation(confirm=confirm)
            _, payload, headers = self._workspace_request(
                "POST", f"/gmail/v1/users/{quote(user_id, safe='')}/messages/send",
                json={"raw": raw_message}, mutation=True,
            )
            return self._receipt(status="succeeded", object_id=payload.get("id") if isinstance(payload, dict) else None,
                                 request_id=headers.get("X-Request-Id"), metadata={"thread_id": payload.get("threadId") if isinstance(payload, dict) else None})
        except PermissionError:
            raise
        except ProviderAdapterError as exc:
            return self._handle_mutation_error(exc)

    def create_calendar_event(self, *, calendar_id: str, event: dict[str, Any], confirm: bool = False) -> dict[str, Any]:
        try:
            self._require_mutation(confirm=confirm)
            _, payload, headers = self._workspace_request(
                "POST", f"/calendar/v3/calendars/{quote(calendar_id, safe='')}/events",
                json=event, mutation=True,
            )
            return self._receipt(status="succeeded", object_id=payload.get("id") if isinstance(payload, dict) else None,
                                 request_id=headers.get("X-Request-Id"), metadata={"html_link": payload.get("htmlLink") if isinstance(payload, dict) else None})
        except PermissionError:
            raise
        except ProviderAdapterError as exc:
            return self._handle_mutation_error(exc)

    def append_sheet_values(self, *, spreadsheet_id: str, range_name: str, values: list[list[Any]], confirm: bool = False) -> dict[str, Any]:
        try:
            self._require_mutation(confirm=confirm)
            path = f"/v4/spreadsheets/{quote(spreadsheet_id, safe='')}/values/{quote(range_name, safe='')}"
            _, payload, headers = self._workspace_request(
                "POST", path, params={"valueInputOption": "USER_ENTERED"},
                json={"range": range_name, "majorDimension": "ROWS", "values": values}, mutation=True,
            )
            # Sheets append responses generally identify the updated range rather than a durable operation ID.
            updated = payload.get("updates", {}) if isinstance(payload, dict) else {}
            updated_range = updated.get("updatedRange")
            if not updated_range:
                return self._receipt(status="uncertain", request_id=headers.get("X-Request-Id"),
                                     metadata={"reconciliation_required": True, "reason": "no durable provider object identity", "response": payload})
            return self._receipt(status="succeeded", object_id=updated_range, request_id=headers.get("X-Request-Id"),
                                 metadata={"updated_range": updated_range, "updates": updated})
        except PermissionError:
            raise
        except ProviderAdapterError as exc:
            return self._handle_mutation_error(exc)

    def create_doc(self, *, title: str, confirm: bool = False) -> dict[str, Any]:
        try:
            self._require_mutation(confirm=confirm)
            _, payload, headers = self._workspace_request("POST", "/v1/documents", json={"title": title}, mutation=True)
            return self._receipt(status="succeeded", object_id=payload.get("documentId") if isinstance(payload, dict) else None,
                                 request_id=headers.get("X-Request-Id"))
        except PermissionError:
            raise
        except ProviderAdapterError as exc:
            return self._handle_mutation_error(exc)
