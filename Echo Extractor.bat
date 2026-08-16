@echo off
cd /d "%~dp0"
start "" pythonw "app\echo_extractor.py" || python "app\echo_extractor.py"
