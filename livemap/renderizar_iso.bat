@echo off
setlocal
rem ==== Mapa ISOMETRICO (como projectzomboidmap.com): renderiza y sube a la Pi (NO apaga el PC) ====
rem OMIT: 3 = 16 px por cuadro, ~3 GB (recomendado: igual a la vista de la web que te gusto)
rem       2 = 32 px, ~10 GB   1 = 64 px, ~45 GB   0 = 128 px (la web completa), ~250 GB
set PZ_ROOT=D:\SteamLibrary\steamapps\common\ProjectZomboid
set OUT=D:\pzmap_render
set SQR=2
set OMIT=2
rem Cada vez te pregunta que zona renderizar. Lo ya renderizado se queda: vas sumando zonas.
rem IMPORTANTE: no cambies OMIT despues de la primera zona (todas deben tener la misma calidad).
echo.
echo  Que zona quieres renderizar?
echo    1. Riverside y alrededores (x 3600-8250, y 4800-7600)
echo    2. Muldraugh
echo    3. West Point
echo    4. Rosewood
echo    5. March Ridge
echo    6. Louisville
echo    7. Valley Station
echo    8. Escribir coordenadas (las de la web: x1 y1 x2 y2)
echo    9. Todo el mapa (~10 GB)
echo.
set /p OPC="Numero: "
set REGION=
if "%OPC%"=="1" set REGION=riverside
if "%OPC%"=="2" set REGION=muldraugh
if "%OPC%"=="3" set REGION=westpoint
if "%OPC%"=="4" set REGION=rosewood
if "%OPC%"=="5" set REGION=marchridge
if "%OPC%"=="6" set REGION=louisville
if "%OPC%"=="7" set REGION=valleystation
if "%OPC%"=="8" goto :coords
if "%OPC%"=="9" goto :zona_ok
if "%REGION%"=="" ( echo Opcion no valida. & pause & exit /b 1 )
goto :zona_ok
:coords
set /p X1="x1 (esquina, la de la web): "
set /p Y1="y1: "
set /p X2="x2 (esquina opuesta): "
set /p Y2="y2: "
set REGION=t:%X1%,%Y1%,%X2%,%Y2%
:zona_ok
echo Zona: %REGION%
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
python "%~dp0configurar_render.py" "%TOOL%\conf\conf.yaml" "%PZ_ROOT%" "%OUT%" %SQR% %OMIT% "%REGION%" || goto :error
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
echo Listo. Revisa %LOG%
echo Fin: %date% %time% >> "%LOG%"
pause
exit /b 0

:error
echo ERROR en el render: %date% %time% >> "%LOG%"
echo.
echo Algo fallo; NO se apaga el PC. Revisa el mensaje de arriba.
pause
exit /b 1
