"""Turn an uploaded document into passages that can be cited.

Every design choice here exists to make one thing true: a quotation in
the knowledge base is text that was actually in a file somebody
uploaded, and not text a model produced.

So the file is stored whole with its digest. It is split by this code,
not by a model. Each passage is stored verbatim with its own digest and
its page numbers. Later, when a candidate unit cites a passage, it cites
a chunk id and the server reads the text out of this table. The model
never has an opportunity to write a quotation, which is a stronger
guarantee than checking one.

Chunking is by paragraph with a size target rather than by fixed
character count. A rule about residency split across two chunks is a
rule neither chunk states, and a reviewer reading half of it cannot
verify anything.
"""

import hashlib
import io
import re
import uuid

# Big enough to hold a complete rule with its conditions, small enough
# that a reviewer can read one and decide.
TARGET_CHARACTERS = 1400
MAX_CHARACTERS = 2600
MIN_CHARACTERS = 120

PDF = "application/pdf"
DOCX = (
    "application/vnd.openxmlformats-officedocument"
    ".wordprocessingml.document"
)
TEXT = "text/plain"
MARKDOWN = "text/markdown"

SUPPORTED = (PDF, DOCX, TEXT, MARKDOWN)

EXTENSIONS = {
    ".pdf": PDF,
    ".docx": DOCX,
    ".txt": TEXT,
    ".md": MARKDOWN,
}


class DocumentRefused(Exception):
    """This file cannot be turned into citable passages."""


def media_type_for(filename):
    lowered = (filename or "").lower()

    for suffix, media_type in EXTENSIONS.items():
        if lowered.endswith(suffix):
            return media_type

    raise DocumentRefused(
        f"{filename!r} is not a kind of document this can read. "
        f"Supported: {', '.join(sorted(EXTENSIONS))}."
    )


def sha256(data):
    if isinstance(data, str):
        data = data.encode("utf-8")

    return hashlib.sha256(data).hexdigest()


def _pages_from_pdf(content):
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise DocumentRefused(
            f"this PDF could not be opened: {exc.__class__.__name__}"
        ) from exc

    pages = []

    for number, page in enumerate(reader.pages, start=1):
        try:
            pages.append((number, page.extract_text() or ""))
        except Exception:  # noqa: BLE001
            pages.append((number, ""))

    return pages


def _pages_from_docx(content):
    import docx

    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise DocumentRefused(
            f"this Word file could not be opened: {exc.__class__.__name__}"
        ) from exc

    # Word has no pages until it is laid out, so the whole body is one
    # page. Saying page 1 for everything is honest; inventing page
    # numbers a reader cannot find in their own copy is not.
    body = "\n\n".join(p.text for p in document.paragraphs if p.text.strip())

    return [(1, body)]


def _pages_from_text(content):
    return [(1, content.decode("utf-8", errors="replace"))]


READERS = {
    PDF: _pages_from_pdf,
    DOCX: _pages_from_docx,
    TEXT: _pages_from_text,
    MARKDOWN: _pages_from_text,
}


def read_pages(content, media_type):
    """(page_number, text) for the whole document."""
    reader = READERS.get(media_type)

    if reader is None:
        raise DocumentRefused(f"no reader for {media_type}")

    return reader(content)


def _tidy(text):
    """Repair the damage PDF extraction does to ordinary prose."""
    # Words split across a line break by hyphenation.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)

    # A single newline inside a sentence is a layout artefact; two mean
    # a paragraph.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


# A sentence end followed by a capital, a digit or an opening bracket.
# Deliberately conservative: splitting "s. 6(1)(a)" would destroy the
# citation the passage exists to carry.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\u201c\"])")


def split_long_block(text):
    """Break an oversized block into whole sentences near the target.

    The last sentence of each piece is repeated as the first of the
    next. A rule that straddles a boundary is then readable in full from
    at least one side, which is what a reviewer needs in order to say
    whether it supports a claim.
    """
    sentences = [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]

    if len(sentences) < 2:
        return [text]

    pieces = []
    current = []
    size = 0

    for sentence in sentences:
        current.append(sentence)
        size += len(sentence) + 1

        if size >= TARGET_CHARACTERS:
            pieces.append(" ".join(current))
            # Overlap by one sentence.
            current = [sentence]
            size = len(sentence) + 1

    remainder = " ".join(current).strip()

    if remainder and (not pieces or remainder != pieces[-1]):
        if len(remainder) < MIN_CHARACTERS and pieces:
            pieces[-1] = pieces[-1] + " " + remainder
        else:
            pieces.append(remainder)

    return pieces


def chunk_pages(pages):
    """Group paragraphs into passages a reviewer can read and verify.

    Returns dicts with the passage, its digest and the page range it
    came from. A paragraph longer than the maximum is kept whole rather
    than cut: a rule split in half is a rule neither half states.
    """
    chunks = []
    buffer = []
    buffer_chars = 0
    page_from = None
    page_to = None

    def flush():
        nonlocal buffer, buffer_chars, page_from, page_to

        if not buffer:
            return

        passage = "\n\n".join(buffer).strip()

        if len(passage) >= MIN_CHARACTERS:
            chunks.append(
                {
                    "ordinal": len(chunks) + 1,
                    "passage": passage,
                    "passage_sha256": sha256(passage),
                    "page_from": page_from,
                    "page_to": page_to,
                }
            )

        buffer = []
        buffer_chars = 0
        page_from = None
        page_to = None

    for number, raw in pages:
        text = _tidy(raw)

        if not text:
            continue

        blocks = []

        for block in (p.strip() for p in text.split("\n\n")):
            if not block:
                continue

            if len(block) > MAX_CHARACTERS:
                blocks.extend(split_long_block(block))
            else:
                blocks.append(block)

        for paragraph in blocks:
            if not paragraph:
                continue

            if buffer_chars and buffer_chars + len(paragraph) > MAX_CHARACTERS:
                flush()

            if page_from is None:
                page_from = number

            page_to = number
            buffer.append(paragraph)
            buffer_chars += len(paragraph) + 2

            if buffer_chars >= TARGET_CHARACTERS:
                flush()

    flush()

    return chunks


def prepare(content, filename):
    """Everything derivable from the file itself, before any model runs."""
    media_type = media_type_for(filename)

    if not content:
        raise DocumentRefused("the file is empty")

    pages = read_pages(content, media_type)
    chunks = chunk_pages(pages)

    if not chunks:
        raise DocumentRefused(
            "no readable text was found. A scanned PDF with no text "
            "layer needs to be run through OCR before it can teach "
            "anything, because there is nothing here to quote."
        )

    return {
        "document_id": str(uuid.uuid4()),
        "media_type": media_type,
        "byte_count": len(content),
        "content_sha256": sha256(content),
        "page_count": len(pages),
        "extracted_characters": sum(len(c["passage"]) for c in chunks),
        "chunks": chunks,
    }
