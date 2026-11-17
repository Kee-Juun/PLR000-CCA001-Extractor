"""Regress the startup delay, bootloader handoff, and optional source execution."""
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import MagicMock, patch

from utils import startup_feedback


class StartupFeedbackTests(unittest.TestCase):
    def test_entrypoint_import_does_not_load_heavy_dependencies(self):
        script = ("import main, sys; "
                  "assert not any(name in sys.modules for name in "
                  "('PyQt6', 'gui.main_window', 'openpyxl', 'selenium'))")
        subprocess.run([sys.executable, '-c', script], cwd=Path(__file__).resolve().parents[1], check=True)

    def test_real_milestones_reach_the_bootloader(self):
        backend = MagicMock()
        backend.is_alive.return_value = True
        with patch.object(startup_feedback, 'pyi_splash', backend), patch.object(startup_feedback, '_closed', False):
            startup_feedback.report_startup('Loading interface libraries...')
            backend.update_text.assert_called_once_with('@stage:Loading interface libraries...')

    def test_close_is_idempotent_and_late_updates_are_ignored(self):
        backend = MagicMock()
        with patch.object(startup_feedback, 'pyi_splash', backend), patch.object(startup_feedback, '_closed', False):
            startup_feedback.close_startup()
            startup_feedback.close_startup()
            startup_feedback.report_startup('Too late')
            backend.close.assert_called_once()
            backend.update_text.assert_called_once_with('@stage:100|Ready')

    def test_percentage_milestones_reach_the_bootloader_and_are_bounded(self):
        backend = MagicMock()
        backend.is_alive.return_value = True
        with patch.object(startup_feedback, 'pyi_splash', backend), patch.object(startup_feedback, '_closed', False):
            for value, expected in ((64, 64), (-2, 0), (101, 100)):
                startup_feedback.report_startup('Loading...', value)
                backend.update_text.assert_called_with(f'@stage:{expected}|Loading...')

    def test_source_runs_and_suppressed_splashes_stay_safe(self):
        with patch.object(startup_feedback, 'pyi_splash', None), patch.object(startup_feedback, '_closed', False):
            startup_feedback.report_startup('Source execution')
            startup_feedback.close_startup()
        backend = MagicMock()
        backend.is_alive.return_value = False
        with patch.object(startup_feedback, 'pyi_splash', backend), patch.object(startup_feedback, '_closed', False):
            startup_feedback.report_startup('Suppressed')
            backend.update_text.assert_not_called()

    def test_broken_status_channel_does_not_break_the_application(self):
        backend = MagicMock()
        backend.is_alive.return_value = True
        backend.update_text.side_effect = OSError('Closed socket')
        backend.close.side_effect = RuntimeError('Closed splash')
        with patch.object(startup_feedback, 'pyi_splash', backend), patch.object(startup_feedback, '_closed', False):
            startup_feedback.report_startup('Loading...')
            startup_feedback.close_startup()

    @unittest.skipUnless(sys.platform == 'win32', 'Windows startup error dialog')
    def test_startup_error_closes_splash_and_shows_actionable_failure(self):
        backend = MagicMock()
        with patch.object(startup_feedback, 'pyi_splash', backend), \
             patch.object(startup_feedback, '_closed', False), \
             patch('pathlib.Path.mkdir'), patch('pathlib.Path.write_text'), \
             patch('ctypes.windll.user32.MessageBoxW') as dialog:
            startup_feedback.show_startup_error(RuntimeError('Test startup failure'))
            backend.close.assert_called_once()
            dialog.assert_called_once()
            self.assertIn('Test startup failure', dialog.call_args.args[1])
            self.assertIn('startup-error.log', dialog.call_args.args[1])


class IntroHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_splash_handoff_waits_for_a_painted_intro_frame(self):
        from PyQt6.QtGui import QImage, QColor
        from PyQt6.QtTest import QTest
        from PyQt6.QtWidgets import QWidget
        from gui.launch_intro import LaunchIntroOverlay
        target = QWidget()
        target.resize(240, 260)
        frame = QImage(16, 16, QImage.Format.Format_ARGB32)
        frame.fill(QColor('#22d3ee'))
        with patch.object(LaunchIntroOverlay, '_load_motion_frames', return_value=[frame]):
            overlay = LaunchIntroOverlay(target)
        painted, finished = [], []
        overlay.first_frame_shown.connect(lambda: painted.append(True))
        overlay.finished.connect(lambda: finished.append(True))
        try:
            self.assertFalse(painted)
            overlay.start()
            QTest.qWait(100)
            self.assertEqual(painted, [True])
            self.assertFalse(finished)
            QTest.qWait(50)
            self.assertEqual(painted, [True])
            overlay._finish_intro()
            self.assertEqual(finished, [True])
            self.assertTrue(target.isVisible())
        finally:
            target.close()


if __name__ == '__main__':
    unittest.main()
