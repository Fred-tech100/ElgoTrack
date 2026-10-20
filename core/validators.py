import os

from django.core.exceptions import ValidationError

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ATTACHMENT_EXTENSIONS = IMAGE_EXTENSIONS | {".pdf"}

MAGIC = {
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".webp": (b"RIFF",),
    ".pdf": (b"%PDF",),
}


def _check(file, allowed, max_mb):
    ext = os.path.splitext(file.name)[1].lower()
    if ext not in allowed:
        raise ValidationError(f"Unsupported file type. Allowed: {', '.join(sorted(allowed))}.")
    if file.size > max_mb * 1024 * 1024:
        raise ValidationError(f"File is too large (maximum {max_mb} MB).")
    position = file.tell() if hasattr(file, "tell") else 0
    file.seek(0)
    head = file.read(16)
    file.seek(position)
    if not any(head.startswith(sig) for sig in MAGIC[ext]):
        raise ValidationError("The file content does not match its extension.")


def validate_image_file(file):
    _check(file, IMAGE_EXTENSIONS, 2)


def validate_attachment(file):
    _check(file, ATTACHMENT_EXTENSIONS, 5)
