"""Small AppKit building blocks: labels, buttons, stacks and grouped "cards".

Everything uses semantic system colors and the system font, so light/dark mode
and accent colors follow the OS with no extra work.
"""

from AppKit import (
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSBezelStyleRounded,
    NSBox,
    NSBoxCustom,
    NSBoxSeparator,
    NSButton,
    NSColor,
    NSFont,
    NSFontDescriptorSystemDesignRounded,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSLayoutAttributeCenterY,
    NSLayoutAttributeLeading,
    NSLayoutConstraintOrientationHorizontal,
    NSLineBreakByTruncatingTail,
    NSLineBreakByWordWrapping,
    NSMutableAttributedString,
    NSStackView,
    NSStackViewDistributionFill,
    NSTextField,
    NSUserInterfaceLayoutOrientationHorizontal,
    NSUserInterfaceLayoutOrientationVertical,
    NSView,
)
from Foundation import NSMakeRect

CARD_PAD = 14
ROW_HEIGHT = 28


def _is_dark(appearance):
    best = appearance.bestMatchFromAppearancesWithNames_(
        [NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
    return best == NSAppearanceNameDarkAqua


# Grouped-box fill, like System Settings: white on light, a faint lift on dark.
CARD_FILL = NSColor.colorWithName_dynamicProvider_(
    "SnapAskCardFill",
    lambda ap: NSColor.colorWithWhite_alpha_(1.0, 0.055) if _is_dark(ap)
    else NSColor.colorWithWhite_alpha_(1.0, 0.85))
CARD_BORDER = NSColor.colorWithName_dynamicProvider_(
    "SnapAskCardBorder",
    lambda ap: NSColor.colorWithWhite_alpha_(1.0, 0.08) if _is_dark(ap)
    else NSColor.colorWithWhite_alpha_(0.0, 0.08))


def font(size=13, weight=NSFontWeightRegular, rounded=False, mono_digits=False):
    f = (NSFont.monospacedDigitSystemFontOfSize_weight_(size, weight) if mono_digits
         else NSFont.systemFontOfSize_weight_(size, weight))
    if rounded:
        d = f.fontDescriptor().fontDescriptorWithDesign_(NSFontDescriptorSystemDesignRounded)
        if d is not None:
            f = NSFont.fontWithDescriptor_size_(d, size)
    return f


def label(text="", size=13, weight=NSFontWeightRegular, color=None, wrap=False,
          selectable=False):
    v = (NSTextField.wrappingLabelWithString_(text) if wrap
         else NSTextField.labelWithString_(text))
    v.setFont_(font(size, weight))
    v.setTextColor_(color or NSColor.labelColor())
    v.setSelectable_(selectable)
    v.setLineBreakMode_(NSLineBreakByWordWrapping if wrap else NSLineBreakByTruncatingTail)
    v.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return v


def caption(text="", color=None, wrap=False):
    return label(text, 11, color=color or NSColor.secondaryLabelColor(), wrap=wrap)


def header(text):
    return label(text, 13, NSFontWeightSemibold, NSColor.labelColor())


def button(title, target, action, key_equivalent=None):
    b = NSButton.buttonWithTitle_target_action_(title, target, action)
    b.setBezelStyle_(NSBezelStyleRounded)
    if key_equivalent is not None:
        b.setKeyEquivalent_(key_equivalent)
    b.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return b


def link_button(title, target, action, size=11):
    """A borderless, accent-colored text button."""
    b = NSButton.buttonWithTitle_target_action_(title, target, action)
    b.setBordered_(False)
    set_title_color(b, title, NSColor.linkColor(), size)
    b.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return b


def set_title_color(btn, title, color, size=11):
    from AppKit import NSFontAttributeName, NSForegroundColorAttributeName
    s = NSMutableAttributedString.alloc().initWithString_attributes_(
        title, {NSForegroundColorAttributeName: color, NSFontAttributeName: font(size)})
    btn.setAttributedTitle_(s)


def stack(views, vertical=True, spacing=8, align=None, insets=None, width=None):
    """width: give every child of a vertical stack this exact width (NSStackView
    has no "fill" alignment, and a constant survives rows being hidden)."""
    s = NSStackView.stackViewWithViews_(list(views))
    s.setOrientation_(NSUserInterfaceLayoutOrientationVertical if vertical
                      else NSUserInterfaceLayoutOrientationHorizontal)
    s.setSpacing_(spacing)
    s.setDistribution_(NSStackViewDistributionFill)
    s.setAlignment_(align if align is not None else
                    (NSLayoutAttributeLeading if vertical else NSLayoutAttributeCenterY))
    if insets:
        s.setEdgeInsets_(insets)                 # (top, left, bottom, right)
    s.setTranslatesAutoresizingMaskIntoConstraints_(False)
    if width is not None:
        for v in views:
            v.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    return s


def hrow(*views, spacing=8):
    """Horizontal row; a None entry becomes a flexible spacer."""
    parts = [spacer() if v is None else v for v in views]
    return stack(parts, vertical=False, spacing=spacing)


def spacer():
    v = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, 0, 0))
    v.setTranslatesAutoresizingMaskIntoConstraints_(False)
    v.setContentHuggingPriority_forOrientation_(1, NSLayoutConstraintOrientationHorizontal)
    v.setContentCompressionResistancePriority_forOrientation_(1, NSLayoutConstraintOrientationHorizontal)
    return v


def separator():
    b = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 1))
    b.setBoxType_(NSBoxSeparator)
    b.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return b


def card(rows, width, spacing=10):
    """Rounded grouped box, `width` wide, holding `rows` stacked vertically."""
    box = NSBox.alloc().initWithFrame_(NSMakeRect(0, 0, 100, 40))
    box.setBoxType_(NSBoxCustom)
    box.setTitlePosition_(0)                     # NSNoTitle
    box.setCornerRadius_(10)
    box.setBorderWidth_(1)
    box.setBorderColor_(CARD_BORDER)
    box.setFillColor_(CARD_FILL)
    box.setContentViewMargins_((0, 0))
    box.setTranslatesAutoresizingMaskIntoConstraints_(False)
    box.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    inner = stack(rows, spacing=spacing, width=width - 2 * CARD_PAD,
                  insets=(CARD_PAD - 2, CARD_PAD, CARD_PAD - 2, CARD_PAD))
    content = box.contentView()
    content.addSubview_(inner)
    for a, b in ((inner.leadingAnchor(), content.leadingAnchor()),
                 (inner.trailingAnchor(), content.trailingAnchor()),
                 (inner.topAnchor(), content.topAnchor()),
                 (inner.bottomAnchor(), content.bottomAnchor())):
        a.constraintEqualToAnchor_(b).setActive_(True)
    return box


def fixed(view, width=None, height=None):
    if width is not None:
        view.widthAnchor().constraintEqualToConstant_(width).setActive_(True)
    if height is not None:
        view.heightAnchor().constraintEqualToConstant_(height).setActive_(True)
    return view


def min_width(view, width):
    view.widthAnchor().constraintGreaterThanOrEqualToConstant_(width).setActive_(True)
    return view
