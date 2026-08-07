"""PDF Merger — command handlers with optional AI assistance."""

import io
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("kanyoza.plugins.pdf_merger.commands")


class PDFMergerCommands:
    """Handles PDF operations — deterministic + AI-assisted."""

    def __init__(self, supabase_client=None, redis_client=None):
        self.supabase = supabase_client
        self.redis = redis_client

    def list_all(self) -> list[dict]:
        """Return all commands for the command executor registry."""
        return [
            {
                "intent": "merge_pdfs",
                "description": "Merge multiple PDFs into one (no AI)",
                "params": ["files"],
                "example": "Merge these PDFs together",
                "category": "merge",
            },
            {
                "intent": "smart_merge",
                "description": "AI-ordered intelligent merge with content analysis",
                "params": ["files", "brand_id"],
                "example": "Smart merge my quarterly reports",
                "category": "merge",
            },
            {
                "intent": "split_pdf",
                "description": "Split a PDF by page ranges",
                "params": ["file", "pages"],
                "example": "Split PDF pages 1-5,6-10",
                "category": "split",
            },
            {
                "intent": "compress_pdf",
                "description": "Compress a PDF to reduce file size",
                "params": ["file", "quality"],
                "example": "Compress this PDF to 50% quality",
                "category": "compress",
            },
            {
                "intent": "pdf_to_images",
                "description": "Convert PDF pages to PNG images",
                "params": ["file", "pages"],
                "example": "Convert PDF pages 1-3 to images",
                "category": "convert",
            },
        ]

    def handle(self, command: str, params: dict, workspace_id: str = "default") -> dict:
        """Dispatch a command to the correct handler."""
        handlers = {
            "merge_pdfs": self._merge_pdfs,
            "smart_merge": self._smart_merge,
            "split_pdf": self._split_pdf,
            "compress_pdf": self._compress_pdf,
            "pdf_to_images": self._pdf_to_images,
        }

        handler = handlers.get(command)
        if not handler:
            return {"ok": False, "error": f"Unknown command: {command}"}

        try:
            # Resolve file_ids to bytes if provided
            if "file_ids" in params and "files" not in params:
                resolved_files, resolve_errors = self.resolve_files(
                    params["file_ids"], workspace_id
                )
                if not resolved_files:
                    return {"ok": False, "error": "Could not resolve any files", "details": resolve_errors}
                params = {**params, "files": resolved_files}

            return handler(params, workspace_id)
        except ImportError as e:
            return {"ok": False, "error": f"Missing dependency: {e}. Install PyPDF2 and Pillow."}
        except Exception as e:
            logger.error("PDF command '%s' failed: %s", command, e)
            return {"ok": False, "error": str(e)[:200]}

    def resolve_files(self, file_ids: list[str], workspace_id: str = "default") -> tuple[list[bytes], list[str]]:
        """
        Resolve file_ids to actual bytes for processing.
        
        Architecture: file_id → storage lookup → download → bytes
        """
        files = []
        errors = []

        for file_id in file_ids:
            try:
                if self.supabase:
                    storage_path = f"pdf_uploads/{workspace_id}/{file_id}"
                    result = self.supabase.storage.from_("pdf-uploads").list(storage_path)

                    if result and len(result) > 0:
                        filename = result[0].get("name", "")
                        full_path = f"{storage_path}/{filename}"
                        downloaded = self.supabase.storage.from_("pdf-uploads").download(full_path)
                        files.append(downloaded)
                        continue

                errors.append(f"File '{file_id}' not found in storage")
            except Exception as e:
                logger.error("Failed to resolve file %s: %s", file_id, e)
                errors.append(f"File '{file_id}': {str(e)[:50]}")

        return files, errors

    # ═══════════════════════════════════════════════════════════════════
    # Deterministic Commands (No AI)
    # ═══════════════════════════════════════════════════════════════════

    def _merge_pdfs(self, params: dict, workspace_id: str) -> dict:
        """Simple merge — concatenates PDFs in the order provided."""
        from PyPDF2 import PdfMerger

        files = params.get("files", [])
        if not files or len(files) < 2:
            return {"ok": False, "error": "At least 2 PDF files are required to merge"}

        output_filename = params.get("output_name", f"merged_{uuid.uuid4().hex[:8]}.pdf")

        merger = PdfMerger()
        merged_count = 0
        errors = []

        for i, file_data in enumerate(files):
            try:
                if isinstance(file_data, bytes):
                    merger.append(io.BytesIO(file_data))
                elif isinstance(file_data, str) and os.path.isfile(file_data):
                    merger.append(file_data)
                else:
                    errors.append(f"File {i + 1}: unsupported format")
                    continue
                merged_count += 1
            except Exception as e:
                errors.append(f"File {i + 1}: {str(e)[:50]}")

        if merged_count == 0:
            return {"ok": False, "error": "No valid PDFs could be merged", "details": errors}

        output_buffer = io.BytesIO()
        merger.write(output_buffer)
        merger.close()

        output_bytes = output_buffer.getvalue()
        output_size_kb = round(len(output_bytes) / 1024, 1)

        storage_url = None
        if self.supabase:
            try:
                storage_path = f"pdf_merger/{workspace_id}/{output_filename}"
                self.supabase.storage.from_("pdf-outputs").upload(
                    storage_path, output_bytes,
                    {"content-type": "application/pdf"},
                )
                storage_url = self.supabase.storage.from_("pdf-outputs").get_public_url(storage_path)
            except Exception as e:
                logger.warning("Supabase upload failed: %s", e)

        return {
            "ok": True,
            "message": f"✅ Merged {merged_count} PDFs → {output_filename} ({output_size_kb}KB)",
            "data": {
                "output_name": output_filename,
                "size_kb": output_size_kb,
                "pages_merged": merged_count,
                "storage_url": storage_url,
                "errors": errors if errors else None,
            },
        }

    def _split_pdf(self, params: dict, workspace_id: str) -> dict:
        """Split a PDF by page ranges."""
        from PyPDF2 import PdfReader, PdfWriter

        file_data = params.get("file")
        pages_str = params.get("pages", "")

        if not file_data:
            return {"ok": False, "error": "PDF file is required"}
        if not pages_str:
            return {"ok": False, "error": "Page ranges are required (e.g., '1-3,4-6')"}

        ranges = []
        for part in pages_str.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                ranges.append((int(start.strip()) - 1, int(end.strip())))
            else:
                page = int(part.strip()) - 1
                ranges.append((page, page + 1))

        if isinstance(file_data, bytes):
            reader = PdfReader(io.BytesIO(file_data))
        elif isinstance(file_data, str) and os.path.isfile(file_data):
            reader = PdfReader(file_data)
        else:
            return {"ok": False, "error": "Unsupported file format"}

        total_pages = len(reader.pages)
        outputs = []

        for i, (start, end) in enumerate(ranges):
            if start >= total_pages:
                continue
            end = min(end, total_pages)

            writer = PdfWriter()
            for page_num in range(start, end):
                writer.add_page(reader.pages[page_num])

            output_buffer = io.BytesIO()
            writer.write(output_buffer)

            output_name = f"split_{i + 1}_{uuid.uuid4().hex[:6]}.pdf"
            outputs.append({
                "name": output_name,
                "pages": f"{start + 1}-{end}",
                "size_kb": round(len(output_buffer.getvalue()) / 1024, 1),
                "bytes": output_buffer.getvalue(),
            })

        return {
            "ok": True,
            "message": f"✅ Split into {len(outputs)} PDF(s)",
            "data": {
                "total_pages": total_pages,
                "outputs": [{"name": o["name"], "pages": o["pages"], "size_kb": o["size_kb"]} for o in outputs],
            },
        }

    def _compress_pdf(self, params: dict, workspace_id: str) -> dict:
        """Compress a PDF by resampling embedded images."""
        from PyPDF2 import PdfReader, PdfWriter
        from PyPDF2.generic import NameObject as PdfName

        file_data = params.get("file")
        quality = max(10, min(90, int(params.get("quality", 50))))

        if not file_data:
            return {"ok": False, "error": "PDF file is required"}

        if isinstance(file_data, bytes):
            reader = PdfReader(io.BytesIO(file_data))
            original_size = len(file_data)
        elif isinstance(file_data, str) and os.path.isfile(file_data):
            reader = PdfReader(file_data)
            original_size = os.path.getsize(file_data)
        else:
            return {"ok": False, "error": "Unsupported file format"}

        total_pages = len(reader.pages)
        scale = 0.3 + (quality / 100) * 0.7

        writer = PdfWriter()
        images_compressed = 0

        for page_num in range(total_pages):
            page = reader.pages[page_num]
            
            try:
                if '/XObject' in page['/Resources']:
                    xobjects = page['/Resources']['/XObject'].get_object()
                    
                    for obj_name in xobjects:
                        xobj = xobjects[obj_name].get_object()
                        
                        if xobj['/Subtype'] == '/Image':
                            try:
                                from PIL import Image as PILImage
                                
                                image_data = xobj.get_data()
                                img = PILImage.open(io.BytesIO(image_data))
                                
                                new_width = int(img.width * scale)
                                new_height = int(img.height * scale)
                                
                                if new_width > 0 and new_height > 0:
                                    img = img.resize((new_width, new_height), PILImage.LANCZOS)
                                
                                output = io.BytesIO()
                                if img.mode in ('RGBA', 'P'):
                                    img = img.convert('RGB')
                                img.save(output, format='JPEG', quality=quality, optimize=True)
                                
                                compressed_data = output.getvalue()
                                
                                if len(compressed_data) < len(image_data):
                                    xobj._data = compressed_data
                                    xobj[PdfName('/Filter')] = PdfName('/DCTDecode')
                                    images_compressed += 1
                                    
                            except Exception as img_err:
                                logger.debug("Image compression skipped on page %d: %s", page_num + 1, img_err)
            except Exception:
                pass
            
            writer.add_page(page)

        output_buffer = io.BytesIO()
        writer.write(output_buffer)
        writer.close()

        output_bytes = output_buffer.getvalue()
        output_name = f"compressed_{uuid.uuid4().hex[:6]}.pdf"
        output_size_kb = round(len(output_bytes) / 1024, 1)
        original_size_kb = round(original_size / 1024, 1)
        
        compression_ratio = round((1 - len(output_bytes) / original_size) * 100, 1) if original_size > 0 else 0

        storage_url = None
        if self.supabase:
            try:
                storage_path = f"pdf_merger/{workspace_id}/{output_name}"
                self.supabase.storage.from_("pdf-outputs").upload(
                    storage_path, output_bytes,
                    {"content-type": "application/pdf"},
                )
                storage_url = self.supabase.storage.from_("pdf-outputs").get_public_url(storage_path)
            except Exception as e:
                logger.warning("Supabase upload failed: %s", e)

        return {
            "ok": True,
            "message": f"✅ Compressed PDF → {output_name} ({output_size_kb}KB, {compression_ratio}% smaller, {images_compressed} images optimized)",
            "data": {
                "output_name": output_name,
                "original_size_kb": original_size_kb,
                "compressed_size_kb": output_size_kb,
                "compression_ratio": compression_ratio,
                "images_compressed": images_compressed,
                "total_pages": total_pages,
                "quality_used": quality,
                "storage_url": storage_url,
            },
        }

    def _pdf_to_images(self, params: dict, workspace_id: str) -> dict:
        """Convert PDF pages to PNG images."""
        try:
            from PIL import Image
            import fitz
        except ImportError:
            return {"ok": False, "error": "Missing dependency: PyMuPDF (fitz) and Pillow required"}

        file_data = params.get("file")
        pages_str = params.get("pages", "all")

        if not file_data:
            return {"ok": False, "error": "PDF file is required"}

        if isinstance(file_data, bytes):
            doc = fitz.open(stream=file_data, filetype="pdf")
        elif isinstance(file_data, str) and os.path.isfile(file_data):
            doc = fitz.open(file_data)
        else:
            return {"ok": False, "error": "Unsupported file format"}

        if pages_str == "all":
            page_numbers = list(range(len(doc)))
        else:
            page_numbers = []
            for part in pages_str.split(","):
                part = part.strip()
                if "-" in part:
                    start, end = part.split("-", 1)
                    page_numbers.extend(range(int(start) - 1, int(end)))
                else:
                    page_numbers.append(int(part) - 1)

        outputs = []
        for page_num in page_numbers:
            if page_num >= len(doc):
                continue
            page = doc[page_num]
            pix = page.get_pixmap(dpi=150)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

            output_buffer = io.BytesIO()
            img.save(output_buffer, format="PNG")

            output_name = f"page_{page_num + 1}_{uuid.uuid4().hex[:6]}.png"
            outputs.append({
                "name": output_name,
                "page": page_num + 1,
                "size_kb": round(len(output_buffer.getvalue()) / 1024, 1),
                "width": pix.width,
                "height": pix.height,
                "bytes": output_buffer.getvalue(),
            })

        doc.close()

        return {
            "ok": True,
            "message": f"✅ Converted {len(outputs)} page(s) to PNG",
            "data": {
                "images": [{"name": o["name"], "page": o["page"], "size_kb": o["size_kb"], "dimensions": f"{o['width']}x{o['height']}"} for o in outputs],
            },
        }

    # ═══════════════════════════════════════════════════════════════════
    # AI-Assisted Command
    # ═══════════════════════════════════════════════════════════════════

    def _smart_merge(self, params: dict, workspace_id: str) -> dict:
        """AI-ordered intelligent merge."""
        from PyPDF2 import PdfReader, PdfMerger

        files = params.get("files", [])
        brand_id = params.get("brand_id", "default")

        if not files or len(files) < 2:
            return {"ok": False, "error": "At least 2 PDF files are required for smart merge"}

        documents = []
        for i, file_data in enumerate(files):
            try:
                if isinstance(file_data, bytes):
                    reader = PdfReader(io.BytesIO(file_data))
                elif isinstance(file_data, str) and os.path.isfile(file_data):
                    reader = PdfReader(file_data)
                else:
                    documents.append({"index": i, "preview": f"File {i + 1}", "pages": 0, "data": file_data})
                    continue

                preview_parts = []
                for page in reader.pages[:2]:
                    text = page.extract_text() or ""
                    preview_parts.append(text[:500])

                documents.append({
                    "index": i,
                    "preview": " ".join(preview_parts)[:1000],
                    "pages": len(reader.pages),
                    "data": file_data,
                })
            except Exception as e:
                documents.append({"index": i, "preview": f"Unreadable: {str(e)[:50]}", "pages": 0, "data": file_data})

        try:
            doc_descriptions = "\n".join([
                f"Document {d['index'] + 1}: {d['pages']} pages. Preview: {d['preview'][:200]}"
                for d in documents
            ])

            from services.gemini import GeminiService
            gemini_svc = GeminiService()
            ai_response = gemini_svc.generate_raw(
                f"You are organizing PDF documents for merging. Determine the most logical order.\nDocuments:\n{doc_descriptions}\nRespond with JSON: {{\"order\": [1,2,3], \"reasoning\": \"...\", \"suggested_filename\": \"...pdf\"}}",
                temperature=0.2,
            )

            import json as json_mod, re
            ai_decision = None
            if ai_response:
                json_match = re.search(r'\{[^{}]*"order"[^{}]*\}', str(ai_response), re.DOTALL)
                if json_match:
                    try:
                        ai_decision = json_mod.loads(json_match.group())
                    except json_mod.JSONDecodeError:
                        pass
                if ai_decision is None:
                    json_match = re.search(r'\{.*?\}', str(ai_response), re.DOTALL)
                    if json_match:
                        try:
                            ai_decision = json_mod.loads(json_match.group())
                        except json_mod.JSONDecodeError:
                            pass

            if ai_decision is None or not isinstance(ai_decision.get("order"), list):
                ai_decision = None

            if ai_decision is not None:
                order = ai_decision.get("order", [])
                valid_indices = set(range(1, len(documents) + 1))
                if not all(isinstance(i, int) and i in valid_indices for i in order):
                    logger.warning("AI returned invalid order %s — using default", order)
                    ai_decision = None

                filename = ai_decision.get("suggested_filename", "") if ai_decision else ""
                if not isinstance(filename, str) or not filename.endswith(".pdf") or "/" in filename or ".." in filename:
                    if ai_decision:
                        ai_decision["suggested_filename"] = f"smart_merged_{uuid.uuid4().hex[:8]}.pdf"

        except Exception as e:
            logger.warning("AI analysis failed, using default order: %s", e)
            ai_decision = None

        if ai_decision is None:
            ai_decision = {
                "order": list(range(1, len(documents) + 1)),
                "reasoning": "AI unavailable or invalid response — using original document order",
                "suggested_filename": f"merged_{uuid.uuid4().hex[:8]}.pdf",
            }

        ordered_docs = []
        for idx in ai_decision.get("order", []):
            doc = next((d for d in documents if d["index"] == idx - 1), None)
            if doc:
                ordered_docs.append(doc)

        output_filename = ai_decision.get("suggested_filename", f"merged_{uuid.uuid4().hex[:8]}.pdf")

        merger = PdfMerger()
        merged_count = 0
        errors = []

        for doc in ordered_docs:
            try:
                if isinstance(doc["data"], bytes):
                    merger.append(io.BytesIO(doc["data"]))
                elif isinstance(doc["data"], str) and os.path.isfile(doc["data"]):
                    merger.append(doc["data"])
                else:
                    errors.append(f"Document {doc['index'] + 1}: unsupported format")
                    continue
                merged_count += 1
            except Exception as e:
                errors.append(f"Document {doc['index'] + 1}: {str(e)[:50]}")

        if merged_count == 0:
            return {"ok": False, "error": "No valid PDFs could be merged", "details": errors}

        output_buffer = io.BytesIO()
        merger.write(output_buffer)
        merger.close()

        output_bytes = output_buffer.getvalue()
        output_size_kb = round(len(output_bytes) / 1024, 1)

        storage_url = None
        if self.supabase:
            try:
                storage_path = f"pdf_merger/{workspace_id}/{output_filename}"
                self.supabase.storage.from_("pdf-outputs").upload(
                    storage_path, output_bytes,
                    {"content-type": "application/pdf"},
                )
                storage_url = self.supabase.storage.from_("pdf-outputs").get_public_url(storage_path)
            except Exception as e:
                logger.warning("Supabase upload failed: %s", e)

        return {
            "ok": True,
            "message": f"🧠 Smart merged {merged_count} PDFs → {output_filename} ({output_size_kb}KB)",
            "data": {
                "output_name": output_filename,
                "size_kb": output_size_kb,
                "pages_merged": merged_count,
                "ai_order": ai_decision.get("order"),
                "ai_reasoning": ai_decision.get("reasoning"),
                "storage_url": storage_url,
                "errors": errors if errors else None,
            },
        }
