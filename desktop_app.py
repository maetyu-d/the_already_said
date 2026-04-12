#!/usr/bin/env python3
from __future__ import annotations

import threading

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSMakeRect,
    NSMakeSize,
    NSMenu,
    NSMenuItem,
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


class AlreadySaidAppDelegate(NSObject):
    def init(self):
        self = objc.super(AlreadySaidAppDelegate, self).init()
        if self is None:
            return None
        self.server = make_server(host="127.0.0.1", port=0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.window = None
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
        self.window.setContentView_(webview)
        self.window.makeKeyAndOrderFront_(None)
        self.window.makeFirstResponder_(webview)
        NSApp.activateIgnoringOtherApps_(True)

    def applicationShouldTerminateAfterLastWindowClosed_(self, app) -> bool:
        return True

    def applicationWillTerminate_(self, notification) -> None:
        self.server.shutdown()
        self.server.server_close()


try:
    from Foundation import NSURLRequest
except ImportError:  # pragma: no cover
    NSURLRequest = None


def build_menu() -> None:
    menubar = NSMenu.alloc().init()

    app_menu_item = NSMenuItem.alloc().init()
    menubar.addItem_(app_menu_item)
    app_menu = NSMenu.alloc().initWithTitle_("The Already Said")
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit The Already Said", "terminate:", "q")
    app_menu_item.setSubmenu_(app_menu)

    edit_menu_item = NSMenuItem.alloc().init()
    menubar.addItem_(edit_menu_item)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Cut", "cut:", "x")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Copy", "copy:", "c")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Paste", "paste:", "v")
    edit_menu.addItemWithTitle_action_keyEquivalent_("Select All", "selectAll:", "a")
    edit_menu_item.setSubmenu_(edit_menu)

    NSApp.setMainMenu_(menubar)


def main() -> None:
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    build_menu()
    delegate = AlreadySaidAppDelegate.alloc().init()
    app.setDelegate_(delegate)
    AppHelper.runEventLoop()


if __name__ == "__main__":
    main()
