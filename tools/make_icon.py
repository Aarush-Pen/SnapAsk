"""Draw the app icon and build assets/SnapAsk.icns (run by build.sh if missing).

A viewfinder (four corner brackets) around three lines of "text", on a rounded
square with Apple's icon-grid proportions. Plain shapes only, no SF Symbols
(their license doesn't allow use in app icons).
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from AppKit import (
    NSBezierPath,
    NSBitmapImageRep,
    NSCalibratedRGBColorSpace,
    NSColor,
    NSGradient,
    NSGraphicsContext,
    NSLineCapStyleRound,
    NSLineJoinStyleRound,
    NSShadow,
)
from Foundation import NSMakeRect

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "assets" / "SnapAsk.icns"


def rgb(hex_, alpha=1.0):
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, alpha)


def draw_png(path, size=1024):
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, size, size, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)
    s = size / 1024

    # body: 824pt rounded square centered in the 1024 canvas, soft drop shadow
    body = NSMakeRect(100 * s, 100 * s, 824 * s, 824 * s)
    plate = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, 185 * s, 185 * s)
    shadow = NSShadow.alloc().init()
    shadow.setShadowColor_(NSColor.colorWithWhite_alpha_(0, 0.28))
    shadow.setShadowOffset_((0, -10 * s))
    shadow.setShadowBlurRadius_(24 * s)
    NSGraphicsContext.saveGraphicsState()
    shadow.set()
    rgb("2E7BF6").setFill()
    plate.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSGradient.alloc().initWithStartingColor_endingColor_(rgb("7A5CFA"), rgb("1FA2F5")) \
        .drawInBezierPath_angle_(plate, -60)

    white = NSColor.whiteColor()
    # viewfinder corners
    inset, arm, line = 262 * s, 130 * s, 52 * s
    lo, hi = inset, size - inset
    corners = NSBezierPath.bezierPath()
    for (x, y, dx, dy) in ((lo, hi, 1, -1), (hi, hi, -1, -1), (lo, lo, 1, 1), (hi, lo, -1, 1)):
        corners.moveToPoint_((x, y + dy * arm))
        corners.lineToPoint_((x, y))
        corners.lineToPoint_((x + dx * arm, y))
    corners.setLineWidth_(line)
    corners.setLineCapStyle_(NSLineCapStyleRound)
    corners.setLineJoinStyle_(NSLineJoinStyleRound)
    white.setStroke()
    corners.stroke()

    # three lines of "text" inside
    bar_h = 44 * s
    for i, (width, alpha) in enumerate(((330, 1.0), (260, 0.9), (190, 0.8))):
        y = 572 * s - i * 96 * s
        rect = NSMakeRect(347 * s, y - bar_h / 2, width * s, bar_h)
        NSColor.colorWithWhite_alpha_(1.0, alpha).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, bar_h / 2, bar_h / 2).fill()

    NSGraphicsContext.restoreGraphicsState()
    png = rep.representationUsingType_properties_(4, {})       # NSBitmapImageFileTypePNG
    png.writeToFile_atomically_(str(path), True)


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp())
    try:
        master = tmp / "icon_1024.png"
        draw_png(master)
        iconset = tmp / "SnapAsk.iconset"
        iconset.mkdir()
        for pt in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = pt * scale
                name = f"icon_{pt}x{pt}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(master), "--out",
                                str(iconset / name)], check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(OUT)], check=True)
        if len(sys.argv) > 1:                       # optional: keep the 1024 PNG preview
            shutil.copy(master, sys.argv[1])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print(f"wrote {OUT.relative_to(ROOT)} ({os.path.getsize(OUT) // 1024} KB)")


if __name__ == "__main__":
    main()
