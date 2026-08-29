@echo off
setlocal
cd /d "%~dp0\.."
set "PYTHONPATH=%CD%\src"
set "NEWSCLAW_DEMO_PYTHON="

if exist ".venv\Scripts\python.exe" (
  set "NEWSCLAW_DEMO_PYTHON=%CD%\.venv\Scripts\python.exe"
)
if not defined NEWSCLAW_DEMO_PYTHON if exist "%CD%\..\..\NewsClaw\.venv\Scripts\python.exe" (
  set "NEWSCLAW_DEMO_PYTHON=%CD%\..\..\NewsClaw\.venv\Scripts\python.exe"
)
if not defined NEWSCLAW_DEMO_PYTHON if exist "%CD%\..\..\NewsClaw-event-layer\.venv\Scripts\python.exe" (
  set "NEWSCLAW_DEMO_PYTHON=%CD%\..\..\NewsClaw-event-layer\.venv\Scripts\python.exe"
)
if not defined NEWSCLAW_DEMO_PYTHON (
  set "NEWSCLAW_DEMO_PYTHON=python"
)

"%NEWSCLAW_DEMO_PYTHON%" "scripts\newsclaw_event_demo_backend.py" --port 8431

endlocal
