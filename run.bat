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

:: Try to set up and run in virtual environment
set USE_VENV=1
if not exist .venv (
    echo Creating virtual environment (.venv)...
    python -m venv .venv
    if %errorlevel% neq 0 (
        echo [WARNING] Failed to create virtual environment. Falling back to global Python.
        set USE_VENV=0
    )
)

if "%USE_VENV%"=="1" (
    echo Activating virtual environment...
    call .venv\Scripts\activate
    
    if not exist .venv\InstalledDependencies.txt (
        echo Installing dependencies from requirements.txt...
        python -m pip install --upgrade pip
        python -m pip install -r requirements.txt --prefer-binary
        if %errorlevel% neq 0 (
            echo [WARNING] Failed to install dependencies inside venv. Falling back to global Python.
            deactivate >nul 2>&1
            set USE_VENV=0
        ) else (
            echo dependencies_installed > .venv\InstalledDependencies.txt
        )
    )
)

if "%USE_VENV%"=="0" (
    echo [INFO] Running using global Python environment.
    echo Ensuring core dependencies are installed...
    python -m pip install -r requirements.txt --prefer-binary
)

:: Open the browser in a new window
echo Opening web browser to dashboard...
start http://127.0.0.1:8000

:: Run the FastAPI application
echo Starting FastAPI backend server...
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload

if %errorlevel% neq 0 (
    echo [ERROR] Failed to start server.
    pause
)
