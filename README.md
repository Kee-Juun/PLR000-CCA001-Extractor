# PLR000-CCA001 Extractor

An RPA web scraping bot for extracting Lexis Cite information from the Lexis website using Selenium automation and PyQt6 GUI.

## Project Structure

The project is organized in a modular structure for easy maintenance and reusability:

```
PLR000-CCA001 Extractor/
├── main.py                 # Entry point
├── requirements.txt        # Python dependencies
├── Project Guide.txt       # Project requirements
├── config/                 # Configuration modules
│   ├── __init__.py
│   └── credentials.py     # Credential management
├── utils/                  # Utility modules
│   ├── __init__.py
│   ├── file_manager.py    # File and folder management
│   ├── logger.py          # Logging functionality
│   └── excel_handler.py   # Excel file operations
├── automation/             # Automation modules
│   ├── __init__.py
│   └── lexis_scraper.py   # Selenium web scraping
└── gui/                    # GUI modules
    ├── __init__.py
    └── main_window.py     # PyQt6 main window
```

## Installation

1. Install Python 3.8 or higher
2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Install ChromeDriver (if not already installed):
   - ChromeDriver should be in your system PATH, or
   - Selenium will attempt to use the ChromeDriverManager

## Usage

1. Run the application:
   ```bash
   python main.py
   ```

2. Enter your Lexis website credentials (ID and Password)

3. Optionally check "Save Credentials" to save your credentials for future use

4. Click "Generate" to create an Excel template file with the required columns:
   - Column A: LNI
   - Column B: FILENAME
   - Column C: COURT
   - Column D: DECIDED DATE
   - Column E: LEXIS CITE (will be filled by the bot)

5. Fill in your LNI data in the Excel file (Column A)

6. Click "Extract" to start the automation process

7. The bot will:
   - Launch Chrome browser
   - Log into Lexis website
   - Process each LNI from your Excel file
   - Extract Lexis Cite information
   - Update the Excel file with results
   - Save output files in the Downloads/PLR000-CCA001 Results folder

## Output Files

All output files are saved in: `Downloads/PLR000-CCA001 Results/`

- **Excel Output**: `PLR000-CCA001_mm-dd-yyyy_hh:mm:ss AM/PM.xlsx`
- **Log File**: `Log_PLR000-CCA001_mm-dd-yyyy_hh:mm:ss AM/PM.txt`

## Features

- **Modular Architecture**: Easy to maintain and extend
- **PyQt6 GUI**: Modern, user-friendly interface
- **Progress Tracking**: Real-time progress bar with sheen animation
- **Status Updates**: Live status messages during extraction
- **Credential Management**: Optional credential saving
- **Error Handling**: Robust error handling and logging
- **Excel Integration**: Automatic template generation and data processing

## Module Descriptions

### config/credentials.py
Manages saving and loading user credentials to/from JSON file.

### utils/file_manager.py
Handles creation of results folder and generation of output file paths with timestamps.

### utils/logger.py
Provides logging functionality with timestamped entries and file saving.

### utils/excel_handler.py
Handles all Excel operations including:
- Template generation
- Reading LNI data
- Writing Lexis Cite results
- File opening

### automation/lexis_scraper.py
Selenium automation module that:
- Launches and manages Chrome browser
- Handles login process
- Performs LNI searches
- Extracts Lexis Cite information
- Manages browser cleanup

### gui/main_window.py
PyQt6 GUI module providing:
- Credential input fields
- Template generation button
- Extraction button
- Animated progress bar
- Real-time status updates

## Notes

- The bot uses XPath selectors to locate elements on the Lexis website
- Chrome browser will be launched automatically
- The bot waits for elements to appear before interacting with them
- If no results are found for an LNI, "Not Available" will be written to the Excel file
- The browser is automatically closed after extraction completes

## Troubleshooting

- **ChromeDriver Issues**: Ensure ChromeDriver is compatible with your Chrome version
- **Login Failures**: Verify your credentials are correct
- **Element Not Found**: The website structure may have changed - check XPath selectors
- **Excel File Errors**: Ensure the Excel file is not open in another application

