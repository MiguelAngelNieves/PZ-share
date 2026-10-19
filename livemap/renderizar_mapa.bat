@echo off
setlocal
rem ==== Renderiza el mapa del juego (vista superior, como el mapa dentro del juego) con pzmap2dzi ====
rem Ajusta estas tres lineas si hace falta:
set PZ_ROOT=D:\SteamLibrary\steamapps\common\ProjectZomboid
set OUT=D:\pzmap_render
rem Pixeles por cuadro del juego: 2 = buen detalle (~2-3 h, ~300 MB). 4 = mas nitido (varias horas, ~1 GB).
set SQR=2

set TOOL=%~dp0pzmap2dzi
if not exist "%TOOL%\main.py" (
  echo Descargando pzmap2dzi ^(MIT, github.com/cff29546/pzmap2dzi^)...
  powershell -NoProfile -Command "Invoke-WebRequest https://github.com/cff29546/pzmap2dzi/archive/refs/heads/main.zip -OutFile $env:TEMP\pzmap2dzi.zip; Expand-Archive $env:TEMP\pzmap2dzi.zip -DestinationPath '%~dp0' -Force"
  ren "%~dp0pzmap2dzi-main" pzmap2dzi
)
python -m pip install -r "%TOOL%\requirements.txt" || goto :error
python "%~dp0configurar_render.py" "%TOOL%\conf\conf.yaml" "%PZ_ROOT%" "%OUT%" %SQR% || goto :error
pushd "%TOOL%"
python main.py deploy || goto :error
python main.py unpack || goto :error
python main.py render base_top || goto :error
popd
echo Comprimiendo para pasarlo a la Pi...
powershell -NoProfile -Command "Compress-Archive -Path '%OUT%\html\map_data\base_top' -DestinationPath '%OUT%\base_top.zip' -Force"
echo.
echo Listo: %OUT%\html\map_data\base_top   (y %OUT%\base_top.zip para la Pi)
pause
exit /b 0
:error
echo.
echo Algo fallo. Revisa el mensaje de arriba.
pause
exit /b 1
