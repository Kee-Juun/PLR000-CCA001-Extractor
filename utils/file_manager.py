"""
File and folder management module for creating results directory and managing output files.
"""
import os
import sys
import subprocess
from pathlib import Path
from datetime import datetime
import glob


class FileManager:
    """Manages file operations and directory creation."""
    
    RESULTS_FOLDER_NAME = "PLR000-CCA001 Results"
    
    def __init__(self):
        """Initialize the file manager."""
        self.downloads_path = Path.home() / "Downloads"
        self.results_path = self.downloads_path / self.RESULTS_FOLDER_NAME
        self._ensure_results_folder()
    
    def _ensure_results_folder(self):
        """Create results folder if it doesn't exist."""
        if not self.results_path.exists():
            self.results_path.mkdir(parents=True, exist_ok=True)
    
    def get_results_folder(self) -> Path:
        """
        Get the path to the results folder.
        
        Returns:
            Path object to the results folder
        """
        return self.results_path
    
    def generate_run_folder_name(self) -> str:
        """
        Generate run folder name with timestamp including milliseconds.
        
        Returns:
            Formatted folder name string (Windows-safe, no invalid characters)
        """
        now = datetime.now()
        date_str = now.strftime("%m-%d-%Y")
        # Include milliseconds for uniqueness
        time_str = now.strftime("%I-%M-%S")
        ms_str = f"{now.microsecond // 1000:03d}"  # Milliseconds (3 digits)
        am_pm = now.strftime("%p")
        return f"{date_str}_{time_str}-{ms_str}-{am_pm}"
    
    def generate_output_filename(self, prefix: str = "PLR000-CCA001") -> str:
        """
        Generate output filename with timestamp.
        
        Args:
            prefix: Prefix for the filename
            
        Returns:
            Formatted filename string (Windows-safe, no invalid characters)
        """
        now = datetime.now()
        date_str = now.strftime("%m-%d-%Y")
        # Replace colons with hyphens and remove spaces for Windows compatibility
        time_str = now.strftime("%I-%M-%S-%p").replace(" ", "")
        return f"{prefix}_{date_str}_{time_str}"
    
    def create_run_folder(self) -> Path:
        """
        Create a new run subfolder with timestamp.
        
        Returns:
            Path object to the created run folder
        """
        folder_name = self.generate_run_folder_name()
        run_folder = self.results_path / folder_name
        run_folder.mkdir(parents=True, exist_ok=True)
        return run_folder
    
    def find_most_recent_excel_file(self) -> Path:
        """
        Find the most recently modified Excel file across all run subfolders.
        
        Returns:
            Path object to the most recent Excel file, or None if not found
        """
        excel_files = []
        
        # Search in main results folder
        for file in self.results_path.glob("*.xlsx"):
            if file.is_file():
                excel_files.append(file)
        
        # Search in all run subfolders
        for run_folder in self.results_path.iterdir():
            if run_folder.is_dir():
                for file in run_folder.glob("*.xlsx"):
                    if file.is_file():
                        excel_files.append(file)
        
        if not excel_files:
            return None
        
        # Return the most recently modified file
        most_recent = max(excel_files, key=lambda f: f.stat().st_mtime)
        return most_recent
    
    def is_file_locked(self, file_path: Path) -> bool:
        """
        Check if a file is locked (open in another application).
        
        Args:
            file_path: Path to the file to check
            
        Returns:
            True if file is locked, False otherwise
        """
        if not file_path.exists():
            return False
        
        try:
            # On Windows, try to open the file in exclusive mode
            if sys.platform == 'win32':
                # Try to open with openpyxl to check if Excel has it locked
                import openpyxl
                try:
                    wb = openpyxl.load_workbook(file_path, read_only=True)
                    wb.close()
                    return False
                except PermissionError:
                    return True
                except Exception:
                    # If openpyxl fails, try basic file access
                    try:
                        with open(file_path, 'r+b') as f:
                            pass
                        return False
                    except (PermissionError, IOError):
                        return True
            else:
                # For other platforms, try basic file access
                with open(file_path, 'r+b') as f:
                    pass
                return False
        except (PermissionError, IOError):
            return True
    
    def get_output_excel_path(self, run_folder: Path = None) -> Path:
        """
        Get the full path for output Excel file.
        
        Args:
            run_folder: Run folder path (optional, uses results_path if not provided)
            
        Returns:
            Path object for the output Excel file
        """
        filename = self.generate_output_filename() + ".xlsx"
        target_folder = run_folder or self.results_path
        return target_folder / filename
    
    def get_output_log_path(self, run_folder: Path = None) -> Path:
        """
        Get the full path for output log file.
        
        Args:
            run_folder: Run folder path (optional, uses results_path if not provided)
            
        Returns:
            Path object for the output log file
        """
        filename = "Log_" + self.generate_output_filename() + ".txt"
        target_folder = run_folder or self.results_path
        return target_folder / filename
    
    def open_results_folder(self) -> bool:
        """
        Open the results folder in the default file manager.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            folder_path = self.get_results_folder()
            if sys.platform == 'win32':
                os.startfile(str(folder_path))
            elif sys.platform == 'darwin':
                subprocess.run(['open', str(folder_path)])
            else:
                subprocess.run(['xdg-open', str(folder_path)])
            return True
        except Exception as e:
            print(f"Error opening folder: {e}")
            return False

