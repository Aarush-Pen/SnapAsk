"""Screenshot a region with the system `screencapture` tool, at full resolution."""

import logging
import math
import os
import subprocess
import tempfile

import Quartz
from Foundation import NSURL

from .config import MAX_IMAGE_EDGE

log = logging.getLogger(__name__)


def capture_region(x, y, w, h):
    """Screenshot a rect given in points, top-left origin. Returns PNG bytes.

    `screencapture -R` only takes whole points, but the selection can land on
    a half point (one pixel on Retina). So it captures the enclosing whole-point
    rect at the display's native scale, then crops to the exact pixel edges.
    """
    left, top = math.floor(x), math.floor(y)
    right, bottom = math.ceil(x + w), math.ceil(y + h)
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-t", "png", "-R",
             f"{left},{top},{right - left},{bottom - top}", path],
            check=True, capture_output=True,
        )
        scale = _crop_to_pixels(path, x - left, y - top, w, h, right - left)
        _shrink(path)
        with open(path, "rb") as f:
            data = f.read()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if not data:
        raise RuntimeError("empty screenshot (check Screen Recording permission)")
    log.info("captured %.1f×%.1f pt at %gx", w, h, scale)
    return data


def _crop_to_pixels(path, dx, dy, w, h, captured_w_pt):
    """Crop the PNG at `path` in place to the selection's pixel edges. Returns
    the scale (pixels per point) the display captured at."""
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    image = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    pw, ph = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    scale = pw / captured_w_pt
    left, top = round(dx * scale), round(dy * scale)
    right, bottom = round((dx + w) * scale), round((dy + h) * scale)
    if (left, top, right, bottom) == (0, 0, pw, ph):
        return scale                                        # already exact
    rect = Quartz.CGRectMake(left, top, min(right, pw) - left, min(bottom, ph) - top)
    cropped = Quartz.CGImageCreateWithImageInRect(image, rect)
    dest = Quartz.CGImageDestinationCreateWithURL(url, "public.png", 1, None)
    Quartz.CGImageDestinationAddImage(dest, cropped, None)
    if not Quartz.CGImageDestinationFinalize(dest):
        raise RuntimeError("couldn't write the cropped screenshot")
    return scale


def _shrink(path):
    out = subprocess.run(
        ["/usr/bin/sips", "-g", "pixelWidth", "-g", "pixelHeight", path],
        capture_output=True, text=True,
    ).stdout
    dims = [int(line.split()[-1]) for line in out.splitlines() if "pixel" in line]
    if dims and max(dims) > MAX_IMAGE_EDGE:
        subprocess.run(["/usr/bin/sips", "-Z", str(MAX_IMAGE_EDGE), path], capture_output=True)
