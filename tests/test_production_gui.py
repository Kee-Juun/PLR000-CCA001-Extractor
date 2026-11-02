"""Check that the GUI starts normal runs and keeps diagnostics opt-in."""
import os
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch


class ProductionGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gui.main_window import MainWindow
        for patcher in (
            patch('gui.main_window.load_credentials', return_value=None),
            patch('gui.main_window.load_setting', side_effect=lambda _key, default=None: default),
            patch('gui.main_window.save_setting'),
            patch('gui.main_window.remove_setting'),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.window = MainWindow()
        self.addCleanup(self.close_window)

    def close_window(self):
        self.window.extraction_thread = None
        self.window.close()

    def test_normal_operation_is_the_launch_default(self):
        self.assertFalse(self.window.diagnostic_checkbox.isChecked())
        self.assertTrue(self.window.diagnostic_checkbox.isEnabled())

    def test_run_button_forwards_selected_mode_to_the_shared_worker(self):
        window = self.window
        window.id_input.setText('test-user')
        window.password_input.setText('test-password')
        window.recipient_to_input.setPlainText('tester@example.invalid')
        window.source_mode_combo.setCurrentIndex(window.source_mode_combo.findData('manual'))
        window.audit_mode_combo.setCurrentIndex(window.audit_mode_combo.findData('post'))
        source_path = Path.cwd() / 'tmp' / 'gui-test-source.xlsx'
        manager = MagicMock()
        manager.find_most_recent_excel_file.return_value = source_path
        manager.is_file_locked.return_value = False
        for diagnostic in (False, True):
            with self.subTest(diagnostic=diagnostic), \
                 patch('utils.file_manager.FileManager', return_value=manager), \
                 patch('utils.logger.Logger') as logger_class, \
                 patch('gui.main_window.ExtractionThread') as worker_class:
                window.diagnostic_checkbox.setChecked(diagnostic)
                window.start_extraction()
                worker_class.assert_called_once()
                self.assertEqual(worker_class.call_args.kwargs['diagnostic_mode'], diagnostic)
                self.assertEqual(worker_class.call_args.kwargs['excel_path'], source_path)
                worker_class.return_value.start.assert_called_once()
                if diagnostic:
                    messages = [str(call.args[0]) for call in logger_class.return_value.log.call_args_list]
                    self.assertFalse(any('outgoing email will be sent' in message for message in messages))
                    self.assertTrue(any('email is suppressed' in message for message in messages))
                window.extraction_thread = None
                window.reset_ui()


if __name__ == '__main__':
    unittest.main()
