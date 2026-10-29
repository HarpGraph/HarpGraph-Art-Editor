@echo off
echo Starting Layer Editor...
python -m src.main
if errorlevel 1 (
    echo Error running program!
    pause
) else (
    echo Program finished successfully!
)
pause