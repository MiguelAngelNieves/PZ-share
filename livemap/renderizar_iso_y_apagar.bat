@echo off
setlocal
rem ==== Mapa ISOMETRICO (como projectzomboidmap.com): renderiza, sube a la Pi y APAGA el PC ====
rem OMIT: 2 = ~32 px por cuadro (recomendado, liviano)  1 = ~64 px (4x mas disco)  0 = maximo (como la web, enorme)
set PZ_ROOT=D:\SteamLibrary\steamapps\common\ProjectZomboid
set OUT=D:\pzmap_render
set SQR=2
set OMIT=2
set PI=arcangel@100.99.158.90
set LOG=%OUT%\render_log.txt

set TOOL=%~dp0pzmap2dzi
if not exist "%TOOL%\main.py" (
  powershell -NoProfile -Command "Invoke-WebRequest https://github.com/cff29546/pzmap2dzi/archive/refs/heads/main.zip -OutFile $env:TEMP\pzmap2dzi.zip; Expand-Archive $env:TEMP\pzmap2dzi.zip -DestinationPath '%~dp0' -Force"
  ren "%~dp0pzmap2dzi-main" pzmap2dzi
)
if not exist "%OUT%" mkdir "%OUT%"
echo Inicio: %date% %time% > "%LOG%"
rem Que el PC no se suspenda mientras renderiza (con cargador)
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
python -m pip install -q -r "%TOOL%\requirements.txt" || goto :error
python "%~dp0configurar_render.py" "%TOOL%\conf\conf.yaml" "%PZ_ROOT%" "%OUT%" %SQR% %OMIT% || goto :error
pushd "%TOOL%"
python main.py deploy || goto :error
python main.py unpack || goto :error
python main.py render base || goto :error
popd
if not exist "%OUT%\html\map_data\base\layer0.dzi" goto :error
if not exist "%OUT%\html\map_data\base\map_info.json" goto :error
echo Render terminado: %date% %time% >> "%LOG%"

echo Comprimiendo...
tar -czf "%OUT%\base.tgz" -C "%OUT%\html\map_data" base || goto :error

echo Subiendo a la Pi...
scp -o BatchMode=yes -o ConnectTimeout=20 "%OUT%\base.tgz" %PI%:~/pzmap/data/ && ssh -o BatchMode=yes %PI% "cd ~/pzmap/data && mkdir -p tiles && tar -xzf base.tgz -C tiles && rm base.tgz" && echo Subido a la Pi: %date% %time% >> "%LOG%" || echo No se pudo subir a la Pi, hazlo a mano con base.tgz >> "%LOG%"

echo.
echo Listo. El PC se apaga en 60 segundos (forzado). Para cancelar: shutdown /a
echo Apagado programado: %date% %time% >> "%LOG%"
shutdown /s /f /t 60 /c "Render del mapa terminado. Apagado forzado en 60 segundos (cancela con: shutdown /a)"
exit /b 0

:error
echo ERROR en el render: %date% %time% >> "%LOG%"
echo.
echo Algo fallo; NO se apaga el PC. Revisa el mensaje de arriba.
pause
exit /b 1
