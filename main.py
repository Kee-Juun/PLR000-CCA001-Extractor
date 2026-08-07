"""
Main entry point for PLR000-CCA001 Extractor bot.
"""
import sys
from pathlib import Path
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication
from gui.launch_intro import centered_window_rect, play_launch_intro
from gui.main_window import MainWindow
from utils.file_manager import FileManager


def main():
    """Main function to run the application."""
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
    if icon_path.exists():
        window.setWindowIcon(QIcon(str(icon_path)))

    screen = app.primaryScreen()
    target_rect = centered_window_rect(window, screen)
    window.setGeometry(target_rect)
    if hasattr(window, "set_launch_intro_active"):
        window.set_launch_intro_active(True)

    # Keep the real UI hidden until the launch intro morphs into it.
    app.launch_intro_overlay = play_launch_intro(window, screen=screen)
    if hasattr(window, "set_launch_intro_active"):
        app.launch_intro_overlay.finished.connect(
            lambda: window.set_launch_intro_active(False)
        )
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

