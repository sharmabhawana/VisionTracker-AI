@echo off
echo ===================================================
echo   VisionTracker AI - Local Setup and Server Startup
echo ===================================================
echo.

:: Check for python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not added to your PATH.
    echo Please install Python 3.8+ and try again.
    pause
    exit /b 1
)

:: Try to set up and run in virtual environment
if not exist .venv goto create_venv
goto check_venv

:create_venv
echo Creating virtual environment (.venv)...
python -m venv .venv
if errorlevel 1 (
    echo [WARNING] Failed to create virtual environment. Falling back to global Python.
    goto run_global
)

:check_venv
echo Activating virtual environment...
call .venv\Scripts\activate
if errorlevel 1 (
    echo [WARNING] Failed to activate virtual environment. Falling back to global Python.
    goto run_global
)

if exist .venv\InstalledDependencies.txt goto start_server

echo Installing dependencies from requirements.txt...
python -m pip install --upgrade pip
python -m pip install -r requirements.txt --prefer-binary
if errorlevel 1 (
    echo [WARNING] Failed to install dependencies inside venv. Falling back to global Python.
    deactivate >nul 2>&1
    goto run_global
)

echo dependencies_installed > .venv\InstalledDependencies.txt
goto start_server

:run_global
echo [INFO] Running using global Python environment.
echo Ensuring core dependencies are installed...
python -m pip install -r requirements.txt --prefer-binary
if errorlevel 1 (
    echo [WARNING] Failed to install dependencies globally. Proceeding anyway.
)

:start_server
:: Open the browser in a new window
echo Opening web browser to dashboard...
start http://127.0.0.1:8000

:: Run the FastAPI application
echo Starting FastAPI backend server...
python -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
if errorlevel 1 (
    echo [ERROR] Failed to start server.
    pause
)
