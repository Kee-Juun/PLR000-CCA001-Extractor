"""
Main entry point for PLR000-CCA001 Extractor bot.
"""
import sys
from PyQt6.QtWidgets import QApplication
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
    
    window = MainWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

