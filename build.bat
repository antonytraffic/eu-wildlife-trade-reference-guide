@echo off
REM Builds the site (docs/) and the whole-guide PDF.
REM Wraps the MSYS2/Pango setup the PDF step needs on Windows -- see
REM https://doc.courtbouillon.org/weasyprint/stable/first_steps.html#windows
REM if this venv or MSYS2 install ever needs to be redone from scratch.
REM
REM MSYS2's mingw64\bin folder defaults to the path below. If MSYS2 is installed
REM somewhere else, set MSYS2_BIN before running, e.g.
REM   set MSYS2_BIN=C:\msys64\mingw64\bin
REM   build.bat

if not defined MSYS2_BIN set "MSYS2_BIN=D:\Apps\MSYS2\mingw64\bin"

if not exist "venv\Scripts\activate.bat" (
    echo venv not found -- run this first:
    echo   python -m venv venv
    echo   venv\Scripts\activate.bat
    echo   python -m pip install -r requirements.txt
    exit /b 1
)

call venv\Scripts\activate.bat

if not exist "%MSYS2_BIN%\libpango-1.0-0.dll" (
    echo Warning: Pango not found in %MSYS2_BIN%
    echo PDF generation will be skipped if WeasyPrint can't load its native libraries.
) else (
    set "PATH=%VIRTUAL_ENV%\Scripts;%MSYS2_BIN%;%PATH%"
    set "WEASYPRINT_DLL_DIRECTORIES=%MSYS2_BIN%"
)

python build_site.py
if errorlevel 1 exit /b 1

if not exist "docs\EU-Wildlife-Trade-Reference-Guide.pdf" (
    echo.
    echo WARNING: the PDF was not built, so the site's PDF download links are broken.
    echo Fix the WeasyPrint/MSYS2 setup and build again before publishing.
    exit /b 2
)
