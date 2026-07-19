@echo off
echo ===================================================
echo   VisionTracker AI - Local Setup and Server Startup
echo ===================================================
echo.

:: Check for python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python is not installed or not added to your PATH.
    echo Please install Python 3.8+ and try again.
    pause
    exit /b 1
)

:: Check if virtual environment exists, if not, create it
if not exist .venv (
    echo Creating virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate environment and install dependencies
echo Activating virtual environment...
call .venv\Scripts\activate

if not exist .venv\InstalledDependencies.txt (
    echo Installing dependencies from requirements.txt...
    python -m pip install --upgrade pip
    pip install -r requirements.txt
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo dependencies_installed > .venv\InstalledDependencies.txt
)

:: Open the browser in a new window
echo Opening web browser to dashboard...
start http://127.0.0.1:8000

:: Run the FastAPI application
echo Starting FastAPI backend server...
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

pause
