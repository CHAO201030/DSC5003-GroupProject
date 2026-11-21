"""Plain-text extraction for keyword/semantic indexing.

Mandatory formats: TXT, HTML, Markdown.  PDF/DOCX/PPTX/XLSX are supported
when the corresponding optional library is installed.  Binary or unsupported
files simply yield an empty body and are indexed by filename only.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

logger = logging.getLogger(__name__)

TEXT_EXTENSIONS = {
    ".txt", ".text", ".log", ".csv", ".tsv", ".json", ".xml", ".yaml", ".yml",
    ".ini", ".conf", ".cfg", ".toml", ".sql", ".py", ".js", ".ts", ".jsx",
    ".tsx", ".java", ".c", ".h", ".cpp", ".hpp", ".go", ".rs", ".rb", ".php",
    ".sh", ".bash", ".css", ".scss", ".vue", ".srt", ".tex",
}
HTML_EXTENSIONS = {".html", ".htm", ".xhtml"}
MARKDOWN_EXTENSIONS = {".md", ".markdown", ".mdown", ".mkd"}
DOCUMENT_EXTENSIONS = {".pdf", ".docx", ".pptx", ".xlsx"}


@dataclass(slots=True)
class ExtractionResult:
    text: str
    truncated: bool
    method: str


class _HTMLTextExtractor(HTMLParser):
    _SKIP_TAGS = {"script", "style", "noscript", "template", "svg"}
    _BLOCK_TAGS = {
        "p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4",
        "h5", "h6", "section", "article", "header", "footer", "blockquote",
        "pre", "table", "ul", "ol", "hr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")
        elif tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self.parts.append(f" {alt} ")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
        elif tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        return "".join(self.parts)


def _decode(raw: bytes) -> str:
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig", errors="replace")
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _html_to_text(raw: bytes) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(_decode(raw))
        parser.close()
    except Exception:  # pragma: no cover - malformed HTML should never break upload
        logger.warning("HTML parsing failed; falling back to raw text", exc_info=True)
        return _decode(raw)
    return parser.text()


_LINK_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)|\[([^\]]+)\]\([^)]*\)")
_FENCE_RE = re.compile(r"^```.*$", re.MULTILINE)


def _markdown_to_text(raw: bytes) -> str:
    text = _decode(raw)
    text = _FENCE_RE.sub("", text)
    text = _LINK_RE.sub(lambda m: m.group(1) or m.group(2) or "", text)
    return text


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        return ""
    try:
        reader = PdfReader(str(path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        logger.info("PDF extraction failed for %s", path, exc_info=True)
        return ""


def _extract_docx(path: Path) -> str:
    try:
        import docx  # type: ignore
    except Exception:
        return ""
    try:
        document = docx.Document(str(path))
        paragraphs = [p.text for p in document.paragraphs]
        for table in document.tables:
            for row in table.rows:
                paragraphs.append("\t".join(cell.text for cell in row.cells))
        return "\n".join(paragraphs)
    except Exception:
        logger.info("DOCX extraction failed for %s", path, exc_info=True)
        return ""


def _extract_pptx(path: Path) -> str:
    try:
        from pptx import Presentation  # type: ignore
    except Exception:
        return ""
    try:
        presentation = Presentation(str(path))
        chunks: list[str] = []
        for slide in presentation.slides:
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    chunks.append(shape.text)
        return "\n".join(chunks)
    except Exception:
        logger.info("PPTX extraction failed for %s", path, exc_info=True)
        return ""


def _extract_xlsx(path: Path) -> str:
    try:
        from openpyxl import load_workbook  # type: ignore
    except Exception:
        return ""
    try:
        workbook = load_workbook(str(path), read_only=True, data_only=True)
        chunks: list[str] = []
        for sheet in workbook.worksheets:
            for row in sheet.iter_rows(values_only=True):
                cells = [str(value) for value in row if value is not None]
                if cells:
                    chunks.append("\t".join(cells))
        workbook.close()
        return "\n".join(chunks)
    except Exception:
        logger.info("XLSX extraction failed for %s", path, exc_info=True)
        return ""


def _normalise(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(
    path: str | Path,
    *,
    mime_type: str | None = None,
    filename: str | None = None,
    max_bytes: int = 2 * 1024 * 1024,
    max_chars: int = 200_000,
) -> ExtractionResult:
    """Extract indexable plain text from a stored file."""
    path = Path(path)
    name = filename or path.name
    suffix = Path(name).suffix.lower()
    mime = (mime_type or "").lower()

    try:
        with path.open("rb") as handle:
            raw = handle.read(max_bytes + 1)
    except OSError:
        logger.info("Unable to read %s for text extraction", path, exc_info=True)
        return ExtractionResult("", False, "unreadable")

    truncated = len(raw) > max_bytes
    raw = raw[:max_bytes]

    if suffix in HTML_EXTENSIONS or mime in {"text/html", "application/xhtml+xml"}:
        text, method = _html_to_text(raw), "html"
    elif suffix in MARKDOWN_EXTENSIONS or mime in {"text/markdown", "text/x-markdown"}:
        text, method = _markdown_to_text(raw), "markdown"
    elif suffix == ".pdf" or mime == "application/pdf":
        text, method = _extract_pdf(path), "pdf"
    elif suffix == ".docx":
        text, method = _extract_docx(path), "docx"
    elif suffix == ".pptx":
        text, method = _extract_pptx(path), "pptx"
    elif suffix == ".xlsx":
        text, method = _extract_xlsx(path), "xlsx"
    elif suffix in TEXT_EXTENSIONS or mime.startswith("text/") or mime in {
        "application/json", "application/xml", "application/javascript",
    }:
        text, method = _decode(raw), "text"
    else:
        # Unknown binary format: fall back to filename-only indexing.
        return ExtractionResult("", truncated, "binary")

    if method == "text" and b"\x00" in raw[:8192]:
        return ExtractionResult("", truncated, "binary")

    text = _normalise(text)
    if len(text) > max_chars:
        text = text[:max_chars]
        truncated = True
    return ExtractionResult(text, truncated, method)
