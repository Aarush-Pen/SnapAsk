"""The settings window: one small, fixed-size, native AppKit window.

It holds no state of its own. Every control calls into the app, and the app
calls render() with a fresh snapshot after every change.
"""

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleWarning,
    NSApp,
    NSBackingStoreBuffered,
    NSBezelStyleAccessoryBarAction,
    NSButtonTypePushOnPushOff,
    NSColor,
    NSFontWeightSemibold,
    NSLayoutConstraintOrientationHorizontal,
    NSMenuItem,
    NSOnState,
    NSOffState,
    NSPopUpButton,
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
    NSSecureTextField,
    NSSegmentDistributionFillEqually,
    NSSegmentedControl,
    NSSegmentSwitchTrackingSelectOne,
    NSTextField,
    NSView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskTitled,
    NSControlSizeLarge,
    NSControlSizeSmall,
)
from Foundation import NSMakeRect, NSObject

from .. import APP_NAME, __version__
from ..hotkeys import ACTIONS
from .. import effort_capabilities as effort_caps
from ..providers import ORDER, PROVIDERS
from . import widgets as W

WIDTH = 440
MARGIN = 20
INNER = WIDTH - 2 * MARGIN


def activate_app():
    # Deprecated, but the macOS 14+ cooperative NSApp.activate() is refused
    # when a menu bar app is woken by a global hotkey, leaving the window unfocused.
    NSApp.activateIgnoringOtherApps_(True)


def money(x):
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


def small_money(x):
    return f"${x:,.4f}" if x < 1 else f"${x:,.2f}"


class SettingsWindowController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(SettingsWindowController, self).init()
        if self is None:
            return None
        self.app = app
        self._build()
        return self

    # --- building -------------------------------------------------------------
    @objc.python_method
    def _build(self):
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable)
        win = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, WIDTH, 600), style, NSBackingStoreBuffered, False)
        win.setTitle_(APP_NAME)
        win.setReleasedWhenClosed_(False)
        win.setDelegate_(self)
        self.window = win

        # provider switcher
        self.providerSeg = NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            [PROVIDERS[p].name for p in ORDER], NSSegmentSwitchTrackingSelectOne,
            self, "providerChanged:")
        self.providerSeg.setSegmentDistribution_(NSSegmentDistributionFillEqually)
        self.providerSeg.setControlSize_(NSControlSizeLarge)
        self.providerSeg.setTranslatesAutoresizingMaskIntoConstraints_(False)

        root = W.stack([self.providerSeg, self._balance_card(), W.header("API key"),
                        self._key_card(), W.header("Model"), self._model_card(),
                        W.header("Keyboard shortcuts"), self._hotkey_card(),
                        self._hotkey_footer()],
                       spacing=16, width=INNER)
        views = root.arrangedSubviews()
        for i, v in enumerate(views):          # headers sit close to their card
            if isinstance(v, NSTextField):
                root.setCustomSpacing_afterView_(6, v)
        root.setCustomSpacing_afterView_(6, views[-2])
        footer = self._footer()

        content = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, WIDTH, 600))
        content.addSubview_(root)
        content.addSubview_(footer)
        for a, b, c in ((root.leadingAnchor(), content.leadingAnchor(), MARGIN),
                        (root.topAnchor(), content.topAnchor(), 14),
                        (footer.leadingAnchor(), content.leadingAnchor(), MARGIN)):
            a.constraintEqualToAnchor_constant_(b, c).setActive_(True)
        root.trailingAnchor().constraintEqualToAnchor_constant_(
            content.trailingAnchor(), -MARGIN).setActive_(True)
        footer.trailingAnchor().constraintEqualToAnchor_constant_(
            content.trailingAnchor(), -MARGIN).setActive_(True)
        footer.bottomAnchor().constraintEqualToAnchor_constant_(
            content.bottomAnchor(), -16).setActive_(True)
        footer.topAnchor().constraintGreaterThanOrEqualToAnchor_constant_(
            root.bottomAnchor(), 18).setActive_(True)
        content.widthAnchor().constraintEqualToConstant_(WIDTH).setActive_(True)
        win.setContentView_(content)

        # Fixed size: tall enough for the fullest state, computed once.
        content.layoutSubtreeIfNeeded()
        height = content.fittingSize().height
        win.setContentMinSize_((WIDTH, height))
        win.setContentMaxSize_((WIDTH, height))
        restored = win.setFrameUsingName_("SnapAskSettings")   # last position
        win.setContentSize_((WIDTH, height))
        if not restored:
            win.center()
        win.setFrameAutosaveName_("SnapAskSettings")

    @objc.python_method
    def _balance_card(self):
        self.balanceLabel = W.label("", 28, NSFontWeightSemibold)
        self.balanceLabel.setFont_(W.font(28, NSFontWeightSemibold, rounded=True, mono_digits=True))
        self.balanceCaption = W.caption("", wrap=True)
        self.balanceCaption.setPreferredMaxLayoutWidth_(INNER - 2 * W.CARD_PAD)
        self.balanceCaption.setMaximumNumberOfLines_(2)
        W.fixed(self.balanceCaption, height=30)        # room for two lines, so nothing shifts

        self.startField = NSTextField.textFieldWithString_("")
        self.startField.setPlaceholderString_("e.g. 20.00")
        self.startField.setTarget_(self)
        self.startField.setAction_("setBalance:")
        self.startField.setTranslatesAutoresizingMaskIntoConstraints_(False)
        W.fixed(self.startField, width=76)
        self.startSet = W.button("Set", self, "setBalance:")
        self.startReset = W.link_button("Reset", self, "resetBalance:")
        self.billingLink = W.link_button("Billing ↗", self, "openBilling:")
        self.startRow = W.hrow(W.label("Starting balance", 13), W.label("$", 13),
                               self.startField, self.startSet, None, self.startReset,
                               self.billingLink, spacing=6)
        return W.card([self.balanceLabel, self.balanceCaption, W.separator(), self.startRow],
                      INNER, spacing=6)

    @objc.python_method
    def _key_card(self):
        self.keyField = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 200, 22))
        self.keyField.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.keyField.setTarget_(self)
        self.keyField.setAction_("saveKey:")
        self.keyField.setContentHuggingPriority_forOrientation_(
            1, NSLayoutConstraintOrientationHorizontal)
        self.saveButton = W.button("Save", self, "saveKey:")
        self.keyStatus = W.label("", 12, color=NSColor.secondaryLabelColor())
        self.keyStatus.setContentCompressionResistancePriority_forOrientation_(
            1, NSLayoutConstraintOrientationHorizontal)
        self.keySpinner = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(0, 0, 16, 16))
        self.keySpinner.setStyle_(NSProgressIndicatorStyleSpinning)
        self.keySpinner.setControlSize_(NSControlSizeSmall)
        self.keySpinner.setDisplayedWhenStopped_(False)
        self.keySpinner.setTranslatesAutoresizingMaskIntoConstraints_(False)
        self.getKeyLink = W.link_button("Get a key ↗", self, "openKeys:", size=12)
        self.deleteButton = W.button("Delete key", self, "deleteKey:")
        self.deleteButton.setHasDestructiveAction_(True)
        return W.card([
            W.hrow(self.keyField, self.saveButton),
            W.hrow(self.keySpinner, self.keyStatus, self.getKeyLink, None, self.deleteButton,
                   spacing=6),
        ], INNER)

    @objc.python_method
    def _model_card(self):
        self.modelPopup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 240, 26), False)
        self.modelPopup.setTarget_(self)
        self.modelPopup.setAction_("modelChanged:")
        self.modelPopup.setTranslatesAutoresizingMaskIntoConstraints_(False)
        W.fixed(self.modelPopup, width=250)

        # A popup, not a segmented control: models offer 3 to 6 levels.
        self.effortPopup = NSPopUpButton.alloc().initWithFrame_pullsDown_(
            NSMakeRect(0, 0, 250, 26), False)
        self.effortPopup.setTarget_(self)
        self.effortPopup.setAction_("effortChanged:")
        self.effortPopup.setTranslatesAutoresizingMaskIntoConstraints_(False)
        W.fixed(self.effortPopup, width=250)
        self.effortCaption = W.caption("")
        return W.card([
            W.hrow(W.label("Model"), None, self.modelPopup),
            W.separator(),
            W.fixed(W.hrow(W.label("Effort"), None, self.effortPopup), height=26),  # same height when hidden
            W.hrow(None, self.effortCaption),
        ], INNER, spacing=8)

    @objc.python_method
    def _hotkey_card(self):
        rows, self.hotkeyButtons = [], {}
        for n, action in enumerate(ACTIONS):
            b = W.button("", self, "hotkeyClicked:")
            b.setBezelStyle_(NSBezelStyleAccessoryBarAction)
            b.setButtonType_(NSButtonTypePushOnPushOff)
            b.setFont_(W.font(12, mono_digits=True))
            b.setTag_(n)
            b.setToolTip_("Click, then press the new shortcut (⎋ cancels)")
            W.min_width(b, 64)
            self.hotkeyButtons[action.id] = b
            rows.append(W.hrow(W.label(action.title), None, b))
        for title, how in (("Select a region", "Drag (in screenshot mode)"),
                           ("Cancel a selection", "⎋ or a click"),
                           ("Copy the answer", "Click the answer")):
            rows.append(W.hrow(W.label(title), None,
                               W.label(how, 12, color=NSColor.secondaryLabelColor())))
        return W.card(rows, INNER, spacing=6)

    @objc.python_method
    def _hotkey_footer(self):
        self.hotkeyCaption = W.caption("")
        self.restoreLink = W.link_button("Restore defaults", self, "restoreHotkeys:")
        return W.hrow(self.hotkeyCaption, None, self.restoreLink)

    @objc.python_method
    def _footer(self):
        self.permLink = W.link_button("", self, "openPermissions:", size=12)
        self.footerCaption = W.caption("Right-click the menu bar icon for quick options")
        self.footerCaption.setToolTip_(f"{APP_NAME} {__version__}")
        self.quitButton = W.button(f"Quit {APP_NAME}", self, "quit:")
        return W.hrow(self.permLink, self.footerCaption, None, self.quitButton)

    # --- showing --------------------------------------------------------------
    @objc.python_method
    def show(self):
        NSApp.unhide_(None)
        activate_app()
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()

    @objc.python_method
    def hide(self):
        self.app.cancel_recording()
        self.window.orderOut_(None)
        if NSApp.isActive():
            NSApp.hide_(None)             # hand focus back to the app you were in

    @objc.python_method
    def is_visible(self):
        return bool(self.window.isVisible())

    @objc.python_method
    def toggle(self):
        if self.is_visible() and self.window.isKeyWindow():
            self.hide()
        else:
            self.show()

    def windowShouldClose_(self, sender):
        self.hide()                       # closing only hides; the app keeps running
        return False

    # --- rendering --------------------------------------------------------------
    @objc.python_method
    def render(self, s):
        prov = PROVIDERS[s["provider"]]
        self.providerSeg.setSelectedSegment_(ORDER.index(prov.id))
        self._render_balance(s, prov)
        self._render_key(s, prov)
        self._render_model(s)
        self._render_hotkeys(s)
        missing = s["missing_permissions"]
        self.permLink.setHidden_(not missing)
        self.footerCaption.setHidden_(bool(missing))
        if missing:
            what = missing[0] if len(missing) == 1 else f"{len(missing)} permissions"
            W.set_title_color(self.permLink, f"⚠ {what} needed · Open Settings…",
                              NSColor.systemOrangeColor(), 12)
            self.permLink.setToolTip_("Needed: " + " and ".join(missing))

    @objc.python_method
    def _render_balance(self, s, prov):
        b = s["balance"]
        tracked = "Estimated · tracked locally"
        for control in (self.startField, self.startSet):
            control.setEnabled_(s["has_key"])
        if not s["has_key"]:
            self.balanceLabel.setStringValue_("API key needed")
            self.balanceLabel.setFont_(W.font(22, NSFontWeightSemibold))
            self.balanceLabel.setTextColor_(NSColor.systemOrangeColor())
            self.balanceCaption.setStringValue_(
                f"Paste your {prov.vendor} API key below to use {prov.name}.")
            self.startField.setStringValue_("")
            self.startReset.setHidden_(True)
            return
        self.balanceLabel.setFont_(W.font(28, NSFontWeightSemibold, rounded=True, mono_digits=True))
        parts = [tracked]
        if b["start"] is None:
            self.balanceLabel.setStringValue_("No balance set")
            self.balanceLabel.setTextColor_(NSColor.secondaryLabelColor())
            parts = [f"Enter your balance from the {prov.vendor} billing page below", tracked]
            if b["spent"]:
                parts.append(f"spent so far {small_money(b['spent'])}")
        else:
            remaining = b["start"] - b["spent"]
            self.balanceLabel.setStringValue_(f"{money(remaining)} remaining")
            self.balanceLabel.setTextColor_(NSColor.systemRedColor() if remaining < 0
                                            else NSColor.labelColor())
            if b["spent"]:
                parts.append(f"{small_money(b['spent'])} spent since you set it")
        if b["last_cost"] is not None:
            parts.append(f"last request {small_money(b['last_cost'])}")
        if b["unpriced_model"]:
            parts.append(f"no price data for {b['unpriced_model']}, not counted")
        self.balanceCaption.setStringValue_(" · ".join(parts))
        if self.startField.currentEditor() is None:       # don't clobber typing
            self.startField.setStringValue_("" if b["start"] is None else f"{b['start']:.2f}")
        self.startReset.setHidden_(b["start"] is None and not b["spent"])

    @objc.python_method
    def _render_key(self, s, prov):
        self.keyField.setPlaceholderString_(f"Paste your {prov.vendor} API key ({prov.key_placeholder})")
        if s.get("clear_key_field"):
            self.keyField.setStringValue_("")
        busy = s["key_busy"]
        self.keyField.setEnabled_(not busy)
        self.saveButton.setEnabled_(not busy)
        self.deleteButton.setEnabled_(s["has_key"] and not busy)
        self.keySpinner.setHidden_(not busy)
        if busy:
            self.keySpinner.startAnimation_(None)
        else:
            self.keySpinner.stopAnimation_(None)
        self.getKeyLink.setHidden_(s["has_key"] or busy or bool(s["key_error"]))
        if busy:
            text, color = f"Checking key with {prov.vendor}…", NSColor.secondaryLabelColor()
        elif s["key_error"]:
            text, color = s["key_error"], NSColor.systemRedColor()
        elif s["has_key"]:
            text, color = f"Saved in Keychain · {s['masked_key']}", NSColor.secondaryLabelColor()
        else:
            text, color = "No key saved", NSColor.secondaryLabelColor()
        self.keyStatus.setStringValue_(text)
        self.keyStatus.setTextColor_(color)
        self.keyStatus.setToolTip_(text)

    @objc.python_method
    def _render_model(self, s):
        popup = self.modelPopup
        menu = popup.menu()
        menu.removeAllItems()
        selected = None
        for m in s["models"]:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(m.label, None, "")
            item.setRepresentedObject_(m.id)
            item.setToolTip_(m.id)
            menu.addItem_(item)
            if m.id == s["model"]:
                selected = item
        if selected is not None:
            popup.selectItem_(selected)
        popup.setToolTip_(f"{s['model']} · " + ("models available to your key" if s["models_live"]
                          else "default list (save a key to load your account's models)"))

        spec = s["effort_spec"]
        popup = self.effortPopup
        menu = popup.menu()
        menu.removeAllItems()
        levels = spec.levels if spec is not None else ()
        for level in levels:
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                effort_caps.label(level, spec.default), None, "")
            item.setRepresentedObject_(level)
            item.setToolTip_(f"Sends “{level}”")
            menu.addItem_(item)
            if level == s["effort"]:
                popup.selectItem_(item)
        popup.setHidden_(not levels)
        if spec is None:
            self.effortCaption.setStringValue_("Effort not configured for this model")
        elif not levels:
            self.effortCaption.setStringValue_("Not supported by this model")
        else:
            self.effortCaption.setStringValue_("")

    @objc.python_method
    def _render_hotkeys(self, s):
        for action in ACTIONS:
            b = self.hotkeyButtons[action.id]
            recording = s["recording"] == action.id
            b.setTitle_("Type shortcut…" if recording else s["hotkeys"][action.id].label)
            b.setState_(NSOnState if recording else NSOffState)
        if s["hotkey_error"]:
            self.hotkeyCaption.setStringValue_(s["hotkey_error"])
            self.hotkeyCaption.setTextColor_(NSColor.systemRedColor())
        else:
            self.hotkeyCaption.setStringValue_(
                "Press the new keys, ⎋ to cancel" if s["recording"]
                else "Click a shortcut to change it")
            self.hotkeyCaption.setTextColor_(NSColor.secondaryLabelColor())
        self.restoreLink.setHidden_(not s["hotkeys_customized"])

    # --- actions ---------------------------------------------------------------
    def providerChanged_(self, sender):
        self.app.set_active_provider(ORDER[sender.selectedSegment()])

    def saveKey_(self, sender):
        key = self.keyField.stringValue().strip()
        if key:
            self.app.save_key(self.app.shown_provider(), key)

    def deleteKey_(self, sender):
        prov = PROVIDERS[self.app.shown_provider()]
        alert = NSAlert.alloc().init()
        alert.setAlertStyle_(NSAlertStyleWarning)
        alert.setMessageText_(f"Delete your {prov.vendor} API key?")
        alert.setInformativeText_(
            f"It's removed from the Keychain and the tracked {prov.name} balance is cleared. "
            "Other providers aren't affected.")
        alert.addButtonWithTitle_("Delete Key").setHasDestructiveAction_(True)
        alert.addButtonWithTitle_("Cancel")
        pid = prov.id

        def done(response):
            if response == NSAlertFirstButtonReturn:
                self.app.delete_key(pid)
        alert.beginSheetModalForWindow_completionHandler_(self.window, done)

    def modelChanged_(self, sender):
        item = sender.selectedItem()
        if item is not None:
            self.app.set_model(self.app.shown_provider(), item.representedObject())

    def effortChanged_(self, sender):
        item = sender.selectedItem()
        if item is not None:
            self.app.set_effort(self.app.shown_provider(), item.representedObject())

    def setBalance_(self, sender):
        text = self.startField.stringValue().strip().replace("$", "").replace(",", "")
        try:
            value = float(text)
        except ValueError:
            NSApp.beep()
            return
        if value < 0:
            NSApp.beep()
            return
        self.window.makeFirstResponder_(None)
        self.app.set_starting_balance(self.app.shown_provider(), value)

    def resetBalance_(self, sender):
        self.app.reset_balance(self.app.shown_provider())
        self.window.makeFirstResponder_(self.startField)

    def openBilling_(self, sender):
        self.app.open_url(PROVIDERS[self.app.shown_provider()].console_url)

    def openKeys_(self, sender):
        self.app.open_url(PROVIDERS[self.app.shown_provider()].keys_url)

    def hotkeyClicked_(self, sender):
        self.app.begin_recording(ACTIONS[sender.tag()].id)

    def restoreHotkeys_(self, sender):
        self.app.restore_default_hotkeys()

    def openPermissions_(self, sender):
        self.app.open_permission_settings()

    def quit_(self, sender):
        self.app.quit()
