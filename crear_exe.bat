@echo off
cd /d "%~dp0"
echo Instalando dependencias...
python -m pip install --upgrade -r requirements.txt pyinstaller
echo Generando PZ Share.exe...
python -m PyInstaller --noconfirm --onefile --windowed --name "PZ Share" --collect-data customtkinter --add-data "mod;mod" --add-data "livemap/static;livemap/static" --add-data "livemap/pzcore.py;livemap" pz_share.py
echo.
echo Listo: dist\PZ Share.exe  (pasaselo a tus amigos, no necesitan Python)
pause
