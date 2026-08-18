@echo off
REM Builds the site (docs/) and the whole-guide PDF.
REM Wraps the MSYS2/Pango setup the PDF step needs on Windows -- see
REM https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows
REM if this venv or MSYS2 install ever needs to be redone from scratch.

set "MSYS2_BIN=D:\Apps\MSYS2\mingw64\bin"

if not exist "venv\Scripts\activate.bat" (
    echo venv not found -- run this first:
    echo   python -m venv venv
    echo   venv\Scripts\activate.bat
    echo   python -m pip install -r requirements.txt
    exit /b 1
)

call venv\Scripts\activate.bat

if not exist "%MSYS2_BIN%\python.exe" (
    echo Warning: MSYS2 mingw64\bin not found at %MSYS2_BIN%
    echo PDF generation will be skipped if WeasyPrint can't load its native libraries.
) else (
    set "PATH=%VIRTUAL_ENV%\Scripts;%MSYS2_BIN%;%PATH%"
    set "WEASYPRINT_DLL_DIRECTORIES=%MSYS2_BIN%"
)

python build_site.py
