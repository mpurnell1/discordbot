@echo off
"%~dp0..\.venv\Scripts\python.exe" -m pytest tests/ -q --tb=short
