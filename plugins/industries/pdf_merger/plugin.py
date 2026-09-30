"""PDF Merger Plugin — Kanyoza AI Platform.

Handles:
  - merge_pdfs: Quick deterministic merge (no AI)
  - smart_merge: AI-ordered intelligent merge
  - split_pdf: Split by page ranges
  - compress_pdf: Reduce file size
  - pdf_to_images: Convert pages to PNG
"""

import logging
from typing import Any

from plugins.industries.base import IndustryPlugin

logger = logging.getLogger("kanyoza.plugins.pdf_merger")


class PDFMergerPlugin(IndustryPlugin):
    pack_id = "pdf_merger"
    pack_version = "1.0"
    display_name = "PDF Document Processing"
    capabilities = ("merge", "split", "compress", "convert", "smart_merge")
    resources = ("documents", "pdf_jobs")
    policies = ("documents.workspace_access",)
    workflows = ("document_merge", "smart_document_merge")
    events = ("document.processed", "pdf_job.completed")

    """
    PDF manipulation plugin with optional AI assistance.

    Deterministic commands (no AI, fast, offline):
      - merge_pdfs, split_pdf, compress_pdf, pdf_to_images

    AI-assisted commands:
      - smart_merge: AI analyzes each document, determines optimal order,
        generates a merged filename based on content.
    """

    def __init__(self, supabase_client=None, redis_client=None):
        super().__init__(supabase_client, redis_client)
        from .commands import PDFMergerCommands
        self.commands_handler = PDFMergerCommands(supabase_client, redis_client)

    def get_commands(self) -> list[dict]:
        return self.commands_handler.list_all()

    def execute(
        self, command: str, params: dict, workspace_id: str = "default"
    ) -> dict:
        return self.commands_handler.handle(command, params, workspace_id)

    def get_webhooks(self) -> list[dict]:
        return [
            {
                "path": "/webhooks/pdf/upload",
                "method": "POST",
                "handler": "handle_pdf_upload",
                "description": "Upload PDFs for merging via external form",
            },
        ]

    def handle_pdf_upload(self, data: dict) -> dict:
        """Handle incoming PDF upload webhook."""
        workspace_id = data.get("workspace_id", "default")
        command = data.get("command", "merge_pdfs")
        result = self.commands_handler.handle(command, data, workspace_id)
        logger.info("PDF webhook processed: %s", result.get("message"))
        return result
