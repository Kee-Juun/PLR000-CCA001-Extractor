"""
Logging module for tracking automation actions and saving logs.
"""
import os
from pathlib import Path
from datetime import datetime
from utils.file_manager import FileManager


class Logger:
    """Handles logging of automation actions."""

    def __init__(self, file_manager: FileManager = None):
        """
        Initialize the logger.

        Args:
            file_manager: FileManager instance for getting log file path
        """
        self.file_manager = file_manager or FileManager()
        self.log_entries = []
        self.log_file_path = None
        self._write_through_enabled = True

    def _append_entry_to_file(self, log_entry: str) -> None:
        """Persist one log entry immediately so mid-run crashes leave a trail."""
        if not self.log_file_path or not self._write_through_enabled:
            return

        try:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file_path, "a", encoding="utf-8") as file:
                file.write(log_entry + "\n")
                file.flush()
                os.fsync(file.fileno())
        except Exception as e:
            print(f"Error appending log entry: {e}")

    def log(self, message: str):
        """
        Add a log entry.

        Args:
            message: Log message to add
        """
        timestamp = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        log_entry = f"[{timestamp}] {message}"
        self.log_entries.append(log_entry)
        print(log_entry)
        self._append_entry_to_file(log_entry)

    def initialize_log_file(self, run_folder: Path = None):
        """
        Initialize the log file path.

        Args:
            run_folder: Run folder path where to save the log (optional)
        """
        self.log_file_path = self.file_manager.get_output_log_path(run_folder)
        try:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_file_path.write_text("", encoding="utf-8")
        except Exception as e:
            print(f"Error initializing log file: {e}")
        self.log("Log file initialized")

    def save_log(self):
        """Save all log entries to file."""
        if self.log_file_path:
            try:
                with open(self.log_file_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(self.log_entries))
                    if self.log_entries:
                        f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                self.log(f"Log saved to: {self.log_file_path}")
            except Exception as e:
                print(f"Error saving log: {e}")

    def get_log_file_path(self) -> Path:
        """
        Get the log file path.

        Returns:
            Path object to the log file
        """
        return self.log_file_path

