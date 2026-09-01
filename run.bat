@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\streamlit.exe" goto run

echo [초기 설정] Python 인터프리터를 찾는 중...
set "PY="
call :try "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
call :try "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
call :try "%USERPROFILE%\anaconda3\python.exe"
call :try "%USERPROFILE%\miniconda3\python.exe"
if not defined PY call :frompath

if not defined PY goto nopython

echo [초기 설정] 사용할 Python: %PY%
"%PY%" -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail

:run
call :findport
echo.
echo   http://localhost:%PORT%  에서 열립니다.
echo   (브라우저가 자동으로 열리지 않으면 위 주소를 직접 입력하세요)
echo.
".venv\Scripts\streamlit.exe" run app.py --server.port %PORT%
exit /b 0

:findport
rem 8501 이 다른 프로그램에 물려 있으면 빈 포트를 찾아 쓴다.
set PORT=8501
:portloop
netstat -ano | find "LISTENING" | find ":%PORT% " >nul
if errorlevel 1 goto :eof
if %PORT% GEQ 8520 goto :eof
echo [알림] 포트 %PORT% 사용 중 — 다음 포트를 시도합니다.
set /a PORT+=1
goto portloop

:try
if defined PY goto :eof
if exist %1 set "PY=%~1"
goto :eof

:frompath
rem PATH 의 python 은 Microsoft Store 스텁일 수 있다. WindowsApps 경로는 건너뛴다.
for /f "delims=" %%P in ('where python 2^>nul') do call :checkpath "%%P"
goto :eof

:checkpath
if defined PY goto :eof
echo %~1 | find /i "WindowsApps" >nul
if errorlevel 1 set "PY=%~1"
goto :eof

:nopython
echo.
echo [오류] 사용 가능한 Python 을 찾지 못했습니다.
echo        python.org 에서 3.11 이상을 설치하세요.
echo        설치 시 "Add python.exe to PATH" 를 체크해야 합니다.
pause
exit /b 1

:fail
echo.
echo [오류] 환경 구성에 실패했습니다. 위 메시지를 확인하세요.
pause
exit /b 1
