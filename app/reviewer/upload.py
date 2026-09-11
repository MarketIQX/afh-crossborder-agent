"""Read one uploaded file out of a multipart request body.

The console has no framework, which has been a feature: there is no
middleware doing something surprising between a request and a database
privilege. It does mean parsing this by hand.

Written defensively because this is the first surface that accepts bytes
from outside. Every limit is explicit and every refusal says which limit
was hit, because "upload failed" is the least useful sentence in
software.
"""

import email.parser
import email.policy

# A statute extract or a practice note. Well above what those need and
# far below what would exhaust memory on a laptop.
MAX_BYTES = 12 * 1024 * 1024

MAX_FIELD_BYTES = 64 * 1024

MAX_FILENAME = 180


class UploadRefused(Exception):
    """The request body could not be accepted."""


def _clean_filename(raw):
    """Keep the name recognisable and refuse anything path-shaped.

    A filename from a browser is attacker-controlled text. It is only
    ever stored and displayed here, never used to open anything, but a
    name containing a path separator would still be wrong in a list and
    wrong in an audit record.
    """
    name = (raw or "").strip().replace("\x00", "")

    # Windows and POSIX separators, and any traversal.
    for bad in ("\\", "/", ".."):
        name = name.replace(bad, "_")

    name = name.strip("._ ") or "unnamed"

    return name[:MAX_FILENAME]


def read_multipart(headers, rfile):
    """Return (fields, upload) from a multipart/form-data request.

    `upload` is None when no file part was sent, which is a normal thing
    for a form the user submitted empty and not an error here.
    """
    content_type = headers.get("Content-Type") or ""

    if "multipart/form-data" not in content_type.lower():
        raise UploadRefused(
            "this form must be sent as multipart/form-data"
        )

    try:
        length = int(headers.get("Content-Length") or 0)
    except ValueError as exc:
        raise UploadRefused("the request had no readable length") from exc

    if length <= 0:
        raise UploadRefused("the request body was empty")

    if length > MAX_BYTES:
        raise UploadRefused(
            f"that file is {length // (1024 * 1024)} MB. The limit is "
            f"{MAX_BYTES // (1024 * 1024)} MB. A statute extract or a "
            f"practice note is far smaller; something this large is "
            f"usually a scan, which has no text to quote anyway."
        )

    body = rfile.read(length)

    if len(body) != length:
        raise UploadRefused(
            "the upload ended early, so the file would be truncated"
        )

    # Rebuild a MIME document so the standard parser can do the work.
    # Hand-rolling boundary scanning is where this kind of code goes
    # wrong.
    prologue = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n"
    parser = email.parser.BytesParser(policy=email.policy.default)

    try:
        message = parser.parsebytes(prologue.encode("utf-8") + body)
    except Exception as exc:  # noqa: BLE001
        raise UploadRefused(
            f"the upload could not be read: {exc.__class__.__name__}"
        ) from exc

    if not message.is_multipart():
        raise UploadRefused("the upload had no parts in it")

    fields = {}
    upload = None

    for part in message.iter_parts():
        disposition = part.get_content_disposition()

        if disposition != "form-data":
            continue

        name = part.get_param("name", header="content-disposition")

        if not name:
            continue

        filename = part.get_filename()

        if filename is None:
            raw = part.get_payload(decode=True) or b""

            if len(raw) > MAX_FIELD_BYTES:
                raise UploadRefused(f"the field {name!r} is too large")

            fields[name] = raw.decode("utf-8", errors="replace").strip()
            continue

        if upload is not None:
            raise UploadRefused(
                "send one file at a time. Reviewing a document is a "
                "judgment, and a batch invites signing off a pile."
            )

        content = part.get_payload(decode=True) or b""

        if not content:
            raise UploadRefused(f"{filename!r} contained no bytes")

        upload = {
            "field": name,
            "filename": _clean_filename(filename),
            "content": content,
            "byte_count": len(content),
        }

    return fields, upload
