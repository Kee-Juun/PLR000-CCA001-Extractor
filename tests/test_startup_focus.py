"""The startup pin ends on user choice and cannot silently re-arm."""
import os
import unittest

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PyQt6.QtCore import QEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QWidget
from gui.startup_focus import StartupFocus


class FakePlatform:
    def __init__(self):
        self.changes = []
        self.notify = None
        self.stopped = 0

    def set_topmost(self, hwnd, enabled):
        self.changes.append(enabled)
        return True

    def watch_outside_click(self, notify):
        self.notify = notify

    def stop_watching(self):
        self.stopped += 1


class StartupFocusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.resize(300, 220)
        self.window.show()
        self.api = FakePlatform()
        self.focus = StartupFocus(self.window, platform_api=self.api)

    def tearDown(self):
        self.window.close()
        QTest.qWait(5)

    def start(self):
        self.focus.after_intro()
        QTest.qWait(10)
        self.assertTrue(self.focus.is_pinned)

    def test_intro_pin_is_deferred_once_and_preserves_geometry(self):
        before = self.window.geometry()
        self.focus.after_intro()
        self.focus.after_intro()
        self.assertFalse(self.focus.is_pinned)
        QTest.qWait(10)
        self.assertEqual(self.api.changes, [True])
        self.assertEqual(self.window.geometry(), before)

    def test_outside_click_releases_and_cannot_rearm(self):
        self.start()
        self.api.notify()
        QTest.qWait(10)
        self.assertFalse(self.focus.is_pinned)
        self.assertEqual(self.api.changes, [True, False])
        self.focus.after_intro()
        QTest.qWait(10)
        self.assertEqual(self.api.changes, [True, False])
        self.assertEqual(self.api.stopped, 1)

    def test_minimize_releases_and_restore_does_not_pin_again(self):
        self.start()
        self.window.showMinimized()
        QTest.qWait(10)
        self.assertFalse(self.focus.is_pinned)
        self.window.showNormal()
        QTest.qWait(10)
        self.assertEqual(self.api.changes, [True, False])

    def test_automatic_deactivation_does_not_release_the_startup_pin(self):
        self.start()
        self.app.sendEvent(self.window, QEvent(QEvent.Type.WindowActivate))
        self.app.sendEvent(self.window, QEvent(QEvent.Type.WindowDeactivate))
        QTest.qWait(10)
        self.assertTrue(self.focus.is_pinned)
        self.app.sendEvent(self.window, QEvent(QEvent.Type.WindowDeactivate))
        QTest.qWait(10)
        self.assertTrue(self.focus.is_pinned)
