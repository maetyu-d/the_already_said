#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import objc
from AppKit import (
    NSSavePanel,
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
    NSOpenPanel,
    NSAlert,
    NSAlertStyleCritical,
    NSModalResponseOK,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSObject, NSURL
from PyObjCTools import AppHelper
from WebKit import WKWebView

from app import make_server
from engine import APP_SUPPORT_DIR, CONFIG_PATH, DEV_DB_PATH, ENV_DB_PATH


class AlreadySaidAppDelegate(NSObject):
    def init(self):
        self = objc.super(AlreadySaidAppDelegate, self).init()
        if self is None:
            return None
        db_path = ensure_external_db_path()
        if db_path is None:
            return None
        os.environ[ENV_DB_PATH] = str(db_path)
        self.server = make_server(host="127.0.0.1", port=0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.window = None
        self.webview = None
        return self

    def applicationDidFinishLaunching_(self, notification) -> None:
        self.server_thread.start()
        host, port = self.server.server_address
        url = NSURL.URLWithString_(f"http://{host}:{port}")

        frame = NSMakeRect(0.0, 0.0, 1500.0, 920.0)
        style = (
            NSWindowStyleMaskTitled
            | NSWindowStyleMaskClosable
            | NSWindowStyleMaskResizable
            | NSWindowStyleMaskMiniaturizable
        )
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame,
            style,
            NSBackingStoreBuffered,
            False,
        )
        self.window.setTitle_("The Already Said")
        self.window.setMinSize_(NSMakeSize(1100.0, 720.0))

        webview = WKWebView.alloc().initWithFrame_(frame)
        request = NSURLRequest.requestWithURL_(url)
        webview.loadRequest_(request)
        self.webview = webview
        self.window.setContentView_(webview)
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(webview)
        NSApp.activateIgnoringOtherApps_(True)

    def applicationShouldTerminateAfterLastWindowClosed_(self, app) -> bool:
        return True

    def applicationWillTerminate_(self, notification) -> None:
        self.server.shutdown()
        self.server.server_close()

    def openDocument_(self, sender) -> None:
        if self.webview is None:
            return

        panel = NSOpenPanel.openPanel()
        panel.setCanChooseFiles_(True)
        panel.setCanChooseDirectories_(False)
        panel.setAllowsMultipleSelection_(False)
        panel.setAllowedFileTypes_(["txt"])
        panel.setTitle_("Open text file")
        panel.setMessage_("Choose a plain text file to load into the draft pane.")
        response = panel.runModal()
        if response != NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return

        text = Path(str(url.path())).read_text(encoding="utf-8")
        script = f"window.alreadySaidApp.setDraftText({json.dumps(text)});"
        self.webview.evaluateJavaScript_completionHandler_(script, None)

    def saveDocumentAs_(self, sender) -> None:
        if self.webview is None:
            return

        panel = NSSavePanel.savePanel()
        panel.setTitle_("Save draft")
        panel.setNameFieldStringValue_("the-already-said.txt")
        panel.setCanCreateDirectories_(True)
        response = panel.runModal()
        if response != NSModalResponseOK:
            return
        url = panel.URL()
        if url is None:
            return
        destination = Path(str(url.path()))

        def completion_handler(result, error) -> None:
            if error is not None or result is None:
                return
            destination.write_text(str(result), encoding="utf-8")

        self.webview.evaluateJavaScript_completionHandler_(
            "window.alreadySaidApp.getDraftText();",
            completion_handler,
        )


try:
    from Foundation import NSURLRequest
except ImportError:  # pragma: no cover
    NSURLRequest = None


def build_menu(delegate: AlreadySaidAppDelegate) -> None:
    menubar = NSMenu.alloc().init()

    app_menu_item = NSMenuItem.alloc().init()
    menubar.addItem_(app_menu_item)
    app_menu = NSMenu.alloc().initWithTitle_("The Already Said")
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit The Already Said", "terminate:", "q")
    app_menu_item.setSubmenu_(app_menu)

    file_menu_item = NSMenuItem.alloc().init()
    menubar.addItem_(file_menu_item)
    file_menu = NSMenu.alloc().initWithTitle_("File")
    open_item = file_menu.addItemWithTitle_action_keyEquivalent_("Open...", "openDocument:", "o")
    open_item.setTarget_(delegate)
    save_item = file_menu.addItemWithTitle_action_keyEquivalent_("Save...", "saveDocumentAs:", "s")
    save_item.setTarget_(delegate)
    file_menu_item.setSubmenu_(file_menu)

    edit_menu_item = NSMenuItem.alloc().init()
    menubar.addItem_(edit_menu_item)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Cut", "cut:", "x")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Copy", "copy:", "c")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Paste", "paste:", "v")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Select All", "selectAll:", "a")
    edit_menu_item.setSubmenu_(edit_menu)

    NSApp.setMainMenu_(menubar)


def read_saved_db_path() -> Path | None:
    if not CONFIG_PATH.exists():
        return None
    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw_path = payload.get("db_path")
    if not raw_path:
        return None
    path = Path(raw_path).expanduser()
    if path.exists():
        return path
    return None


def save_db_path(db_path: Path) -> None:
    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps({"db_path": str(db_path)}, indent=2), encoding="utf-8")


def prompt_for_db_path() -> Path | None:
    panel = NSOpenPanel.openPanel()
    panel.setCanChooseFiles_(True)
    panel.setCanChooseDirectories_(False)
    panel.setAllowsMultipleSelection_(False)
    panel.setAllowedFileTypes_(["db"])
    panel.setTitle_("Choose your Gutenberg index")
    panel.setMessage_("Select the external gutenberg.db file to use with The Already Said.")
    response = panel.runModal()
    if response != NSModalResponseOK:
        return None
    url = panel.URL()
    if url is None:
        return None
    return Path(str(url.path()))


def show_missing_db_alert() -> None:
    alert = NSAlert.alloc().init()
    alert.setAlertStyle_(NSAlertStyleCritical)
    alert.setMessageText_("Gutenberg index not found")
    alert.setInformativeText_(
        "The standalone app now expects an external gutenberg.db file. Choose your existing index when prompted."
    )
    alert.runModal()


def ensure_external_db_path() -> Path | None:
    saved = read_saved_db_path()
    if saved is not None:
        return saved
    if DEV_DB_PATH.exists():
        save_db_path(DEV_DB_PATH)
        return DEV_DB_PATH
    show_missing_db_alert()
    selected = prompt_for_db_path()
    if selected is None or not selected.exists():
        return None
    save_db_path(selected)
    return selected


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = AlreadySaidAppDelegate.alloc().init()
    if delegate is None:
        return
    build_menu(delegate)
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
