"""The one door an uploaded photograph comes through.

Three things happen here, and each of them is a hole if it is skipped.

**The name is ours.** An upload arrives with a filename the browser was given
by whoever made the file. Django's storage will sanitise a path separator, but
the extension is still theirs — and an extension is what a web server uses to
choose a Content-Type. A file called ``recipe.html`` served from ``/media/`` is
HTML from this app's own origin, which is a stored XSS with none of the usual
work. So the stored name is generated from a random token plus an extension
*we* pick from what Pillow says the image actually is.

**The bytes are verified.** ``ImageField`` already runs Pillow's ``verify()``,
which is genuinely a check — but it happens in the form, and the form is not
the only door: a management command, a future import, an API. This module is,
so the check lives here.

**The picture is resized.** A phone photograph is 4-6 MB and is displayed at
about 800 pixels. Storing it whole fills a NAS share with pixels nobody sees
and makes the recipe list slow over mobile data, which on a NAS at the end of a
domestic uplink is the connection that matters.
"""

import io
import secrets
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.utils.translation import gettext_lazy as _

# What we are willing to store, and the extension each one gets. The mapping is
# one-way on purpose: whatever the upload was called, it leaves here as one of
# these four.
FORMATS = {
    "JPEG": ".jpg",
    "PNG": ".png",
    "WEBP": ".webp",
    "GIF": ".gif",
}


def recipe_image_path(instance, filename):
    """Where a recipe photograph is stored. Called by the ImageField.

    Never uses ``filename``. It is the browser's string, it can contain
    anything, and there is nothing in it worth keeping — the recipe's own title
    is the caption, and the file is only ever reached through the recipe row.
    """
    return f"recipes/{secrets.token_urlsafe(12)}{Path(filename).suffix.lower()[:5]}"


def clean_upload(uploaded):
    """Verify, resize and re-wrap an uploaded photograph.

    Returns a file ready to assign to ``Recipe.image``. Raises
    ``ValidationError`` with a sentence somebody can act on — a refused
    photograph must not take the rest of the recipe down with it, so the caller
    keeps the message and saves everything else.
    """
    from PIL import Image, UnidentifiedImageError

    if uploaded.size > settings.IMAGE_MAX_UPLOAD_BYTES:
        raise ValidationError(_("That photograph is too large (maximum %(mb)s MB).") % {
            "mb": settings.IMAGE_MAX_UPLOAD_BYTES // (1024 * 1024),
        })

    try:
        # Opened twice on purpose. verify() is the only call that checks the
        # file is structurally sound, and Pillow documents that the image is
        # unusable afterwards — so the second open is what actually reads it.
        probe = Image.open(uploaded)
        probe.verify()
        uploaded.seek(0)
        image = Image.open(uploaded)
        image.load()
    except (UnidentifiedImageError, OSError, ValueError):
        raise ValidationError(_("That file could not be read as an image."))

    fmt = image.format if image.format in FORMATS else "JPEG"

    # An animated GIF loses its animation to the resize below, and a recipe
    # photograph is not animated. Stored as it arrived rather than silently
    # flattened to its first frame.
    if fmt == "GIF":
        uploaded.seek(0)
        return uploaded

    if image.mode in ("RGBA", "LA", "P") and fmt == "JPEG":
        # JPEG has no alpha channel; Pillow raises rather than choosing a
        # background, so we choose one. White, because a recipe page is white.
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
        image = background
    elif image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")

    edge = settings.IMAGE_MAX_EDGE
    if max(image.size) > edge:
        # thumbnail() is in-place, keeps the aspect ratio and never enlarges,
        # which is all three of the things wanted here.
        image.thumbnail((edge, edge), Image.LANCZOS)

    buffer = io.BytesIO()
    save_kwargs = {"optimize": True}
    if fmt == "JPEG":
        save_kwargs["quality"] = 85
        save_kwargs["progressive"] = True
    image.save(buffer, format=fmt, **save_kwargs)
    buffer.seek(0)

    name = f"{secrets.token_urlsafe(12)}{FORMATS[fmt]}"
    return InMemoryUploadedFile(
        buffer, field_name="image", name=name,
        content_type=Image.MIME.get(fmt, "image/jpeg"),
        size=buffer.getbuffer().nbytes, charset=None,
    )
