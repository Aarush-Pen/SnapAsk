"""Menu bar icon. Left-click toggles the settings window; right-click (or
⌃-click) opens a menu with the provider switcher and Quit."""

import objc
from AppKit import (
    NSApp,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSEventMaskLeftMouseUp,
    NSEventMaskRightMouseUp,
    NSEventTypeRightMouseUp,
    NSImage,
    NSMenu,
    NSMenuItem,
    NSOffState,
    NSOnState,
    NSStatusBar,
    NSVariableStatusItemLength,
)
from Foundation import NSObject

from .. import APP_NAME
from ..hotkeys import KEY_NAMES
from ..providers import ORDER, PROVIDERS

_MASKS = {"ctrl": NSEventModifierFlagControl, "alt": NSEventModifierFlagOption,
          "shift": NSEventModifierFlagShift, "cmd": NSEventModifierFlagCommand}


def _set_shortcut(item, combo):
    """Show a global hotkey next to a menu item (display only)."""
    name = KEY_NAMES.get(combo.key, "")
    if len(name) != 1:
        return
    mask = 0
    for m in combo.mods:
        mask |= _MASKS[m]
    item.setKeyEquivalent_(name.lower())
    item.setKeyEquivalentModifierMask_(mask)


class StatusItemController(NSObject):
    def initWithApp_(self, app):
        self = objc.super(StatusItemController, self).init()
        if self is None:
            return None
        self.app = app
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(NSVariableStatusItemLength)
        button = self.item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "text.viewfinder", APP_NAME)
        image.setTemplate_(True)
        button.setImage_(image)
        button.setToolTip_(APP_NAME)
        button.setTarget_(self)
        button.setAction_("clicked:")
        button.sendActionOn_(NSEventMaskLeftMouseUp | NSEventMaskRightMouseUp)
        self.menu = NSMenu.alloc().initWithTitle_(APP_NAME)
        self.menu.setAutoenablesItems_(False)
        return self

    def clicked_(self, sender):
        event = NSApp.currentEvent()
        if (event is not None and (event.type() == NSEventTypeRightMouseUp
                                   or event.modifierFlags() & NSEventModifierFlagControl)):
            self.show_menu()
        else:
            self.app.toggle_window()

    @objc.python_method
    def show_menu(self):
        self._rebuild()
        self.item.setMenu_(self.menu)
        self.item.button().performClick_(None)     # tracks the menu until it closes
        self.item.setMenu_(None)                    # so left-click stays a click

    @objc.python_method
    def _rebuild(self):
        s = self.app.menu_state()
        m = self.menu
        m.removeAllItems()

        if hasattr(NSMenuItem, "sectionHeaderWithTitle_"):        # macOS 14+
            m.addItem_(NSMenuItem.sectionHeaderWithTitle_("Provider"))
        else:
            m.addItemWithTitle_action_keyEquivalent_("Provider", None, "").setEnabled_(False)
        for n, pid in enumerate(ORDER):
            title = PROVIDERS[pid].name + ("" if s["keys"][pid] else "  ·  no key")
            it = m.addItemWithTitle_action_keyEquivalent_(title, "selectProvider:", "")
            it.setTarget_(self)
            it.setTag_(n)
            it.setState_(NSOnState if pid == s["active"] else NSOffState)
        m.addItem_(NSMenuItem.separatorItem())

        info = m.addItemWithTitle_action_keyEquivalent_(s["summary"], None, "")
        info.setEnabled_(False)
        shot = m.addItemWithTitle_action_keyEquivalent_("Take Screenshot", "capture:", "")
        shot.setTarget_(self)
        _set_shortcut(shot, s["capture_combo"])
        settings = m.addItemWithTitle_action_keyEquivalent_("Settings…", "openSettings:", ",")
        settings.setTarget_(self)
        m.addItem_(NSMenuItem.separatorItem())
        quit_item = m.addItemWithTitle_action_keyEquivalent_(f"Quit {APP_NAME}", "quit:", "q")
        quit_item.setTarget_(self)

    def selectProvider_(self, sender):
        self.app.set_active_provider(ORDER[sender.tag()], announce=True)

    def capture_(self, sender):
        self.app.handle_action("capture")

    def openSettings_(self, sender):
        self.app.show_window()

    def quit_(self, sender):
        self.app.quit()

    @objc.python_method
    def remove(self):
        if self.item is not None:
            NSStatusBar.systemStatusBar().removeStatusItem_(self.item)
            self.item = None
