@echo off
echo Starting Google Chrome with Remote Debugging on 127.0.0.1:9222...
echo.

set PROFILE_DIR=%USERPROFILE%\.devero_chrome_profile

if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%PROFILE_DIR%"
) else if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
    start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%PROFILE_DIR%"
) else (
    echo Chrome executable not found in default paths!
    echo Please start Chrome manually with: chrome.exe --remote-debugging-port=9222 --user-data-dir="%PROFILE_DIR%"
    pause
    exit /b 1
)

echo Chrome started successfully with debug port 9222!
echo Log into Devero in this Chrome window, then run: uv run python devero_copier.py
