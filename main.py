"""
Main entry point for PLR000-CCA001 Extractor bot.
"""
import sys
from pathlib import Path
from utils.startup_feedback import close_startup, report_startup, show_startup_error, startup_is_minimized


def main():
    """Main function to run the application."""
    report_startup('Loading interface libraries...', 56)
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication

    report_startup('Loading application components...', 64)
    from gui.launch_intro import LaunchIntroOverlay, centered_window_rect
    from gui.main_window import MainWindow
    from gui.startup_focus import StartupFocus
    from utils.file_manager import FileManager

    report_startup('Preparing your workspace...', 80)
    # Create results folder on launch
    file_manager = FileManager()
    file_manager.get_results_folder()
    
    # Create and run the application
    app = QApplication(sys.argv)
    app.setStyle('Fusion')  # Modern look

    icon_path = Path(__file__).resolve().parent / "assets" / "app_icon.png"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    
    window = MainWindow()
    window.startup_focus = StartupFocus(window)
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))

    screen = app.primaryScreen()
    target_rect = centered_window_rect(window, screen)
    window.setGeometry(target_rect)
    if hasattr(window, "set_launch_intro_active"):
        window.set_launch_intro_active(True)

    # Keep the real UI hidden until the launch intro morphs into it.
    report_startup('Opening your workspace...', 92)
    app.launch_intro_overlay = LaunchIntroOverlay(window, screen=screen)
    app.launch_intro_overlay.first_frame_shown.connect(close_startup)
    app.launch_intro_overlay.finished.connect(close_startup)
    app.launch_intro_overlay.finished.connect(window.startup_focus.after_intro)
    if hasattr(window, "set_launch_intro_active"):
        app.launch_intro_overlay.finished.connect(
            lambda: window.set_launch_intro_active(False)
        )
    if startup_is_minimized():
        # A user who minimizes loading should not be interrupted by the intro.
        app.launch_intro_overlay.deleteLater()
        if hasattr(window, 'set_launch_intro_active'):
            window.set_launch_intro_active(False)
        window.showMinimized()
        close_startup()
    else:
        app.launch_intro_overlay.start()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        show_startup_error(error)
        sys.exit(1)

