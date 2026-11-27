@echo off
setlocal EnableExtensions
cd /d "%~dp0"

rem ---------------------------------------------------------------- inputs
rem  project folder:  libmpv-2.dll or mpv-2.dll  (release libmpv, x86_64)
rem  project folder:  LICENSE.LGPL  (from the same libmpv zip, goes into the installer)
rem  ffmpeg folder:   ffmpeg.exe, ffprobe.exe, LICENSE  (gyan.dev release essentials)

set MPV_DLL=
if exist "mpv-2.dll" set MPV_DLL=mpv-2.dll
if exist "libmpv-2.dll" set MPV_DLL=libmpv-2.dll
if exist "mpv-2.dll" if exist "libmpv-2.dll" echo [WARNING] Both mpv-2.dll and libmpv-2.dll found - using libmpv-2.dll.
if not defined MPV_DLL (
    echo [ERROR] libmpv-2.dll / mpv-2.dll not found in the project folder.
    goto error
)
if not exist "ffmpeg\ffmpeg.exe" (
    echo [ERROR] ffmpeg\ffmpeg.exe not found.
    goto error
)
if not exist "ffmpeg\ffprobe.exe" (
    echo [ERROR] ffmpeg\ffprobe.exe not found.
    goto error
)
if not exist "ffmpeg\LICENSE" (
    echo [ERROR] ffmpeg\LICENSE not found - copy LICENSE from the FFmpeg archive.
    goto error
)
if not exist "LICENSE.LGPL" (
    echo [ERROR] LICENSE.LGPL not found - copy it from the libmpv zip.
    goto error
)
"ffmpeg\ffmpeg.exe" -hide_banner -filters 2>nul | findstr /C:" zscale " >nul
if errorlevel 1 (
    echo [ERROR] ffmpeg\ffmpeg.exe has no zscale filter - HDR exports would look washed out.
    echo Use the gyan.dev "release essentials" or "release full" build.
    goto error
)
for /f "tokens=3" %%v in ('ffmpeg\ffmpeg.exe -version ^| findstr /B /C:"ffmpeg version"') do set FFMPEG_VERSION=%%v
echo mpv DLL: %MPV_DLL%
echo FFmpeg:  %FFMPEG_VERSION%

rem ------------------------------------------------------------ python env
rem  CI (GitHub Actions sets CI=true): Python and pinned packages come from the
rem  workflow (requirements-lock.txt), no local .venv.
if defined CI goto pyenv_ready
if not exist ".venv\Scripts\python.exe" (
    py -3 -m venv .venv
    if errorlevel 1 goto error
)
call ".venv\Scripts\activate.bat"

python -m pip install --upgrade pip
if errorlevel 1 goto error
python -m pip install -r requirements-build.txt
if errorlevel 1 goto error

:pyenv_ready
for /f %%v in ('python -c "from chikchik.version import VERSION; print(VERSION)"') do set VERSION=%%v

python tools\build_info.py
if errorlevel 1 goto error

rem ---------------------------------------------------------------- program
rem  F build 4: --noupx (never compress DLLs: slower start), runtime hook = early
rem  GL warm before PyInstaller loads QtCore (QTC_EARLY_WARM=0 off), excluded modules
rem  are never imported at run time (their PyInstaller hooks cost ~7 ms at start).
python -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --windowed ^
    --onedir ^
    --noupx ^
    --runtime-hook packaging\windows\pyi_rth_chikchik.py ^
    --exclude-module multiprocessing ^
    --exclude-module pkgutil ^
    --name ChikChik ^
    --add-data "assets\fonts;fonts" ^
    --add-data "assets\icons\icon.ico;." ^
    --icon assets\icons\icon.ico ^
    app.py
if errorlevel 1 goto error

set OUT=dist\ChikChik
rem Always ship the DLL as mpv-2.dll: python-mpv looks for that name first,
rem so ours wins over any mpv-2.dll another program put into PATH.
copy /y "%MPV_DLL%" "%OUT%\mpv-2.dll" >nul
if errorlevel 1 goto error
xcopy /e /i /y /q "ffmpeg" "%OUT%\ffmpeg" >nul
if errorlevel 1 goto error
if exist "%OUT%\ffmpeg\ffplay.exe" del /q "%OUT%\ffmpeg\ffplay.exe"
rem T30: icon of the "ChikChik! -> GIF" Explorer item, next to the exe
copy /y "assets\icons\gif.ico" "%OUT%\gif.ico" >nul
if errorlevel 1 goto error

echo.
echo Program: %CD%\%OUT%\ChikChik.exe

rem -------------------------------------------------------------- installer
set ISCC=
for %%p in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
    "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"
) do if exist %%p set "ISCC=%%~p"
if not defined ISCC for /f "delims=" %%p in ('where ISCC.exe 2^>nul') do set "ISCC=%%p"

if not defined ISCC (
    echo.
    if defined CI (
        echo [ERROR] Inno Setup 6 not found on the CI runner.
        goto error
    )
    echo [WARNING] Inno Setup 6 not found - installer skipped.
    echo Install it:  winget install JRSoftware.InnoSetup
    goto done
)

echo.
echo Building installer (compression takes a minute or two)...
"%ISCC%" /Qp /DAppVersion=%VERSION% packaging\windows\installer.iss
if errorlevel 1 goto error
echo Installer: %CD%\dist\ChikChik-%VERSION%-Setup.exe

:done
echo.
echo Build complete.
if not defined CI pause
exit /b 0

:error
echo.
echo Build failed.
if not defined CI pause
exit /b 1
