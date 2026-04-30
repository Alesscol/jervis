@echo off
echo ============================================
echo     J.E.R.V.I.S. - AVVIO SISTEMI
echo ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo ERRORE: Python non trovato.
    pause & exit /b 1
)

echo [1/3] Installazione dipendenze...
pip install -r requirements.txt --quiet

echo [2/3] Avvio server...
echo [3/3] Apertura browser tra 2 secondi...
echo.
echo Per fermare JERVIS premi CTRL+C
echo.

start /b cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5000"
python app.py
pause
