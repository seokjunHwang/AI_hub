@echo off
rem  ==========================================================
rem   회의록 자동화 GUI - 이 파일을 더블클릭하세요.
rem
rem   파이썬을 찾는 순서 (위가 먼저)
rem     1) .env 의  PYTHON=C:\...python.exe
rem     2) 이 폴더의 .venv
rem     3) PATH 의 python
rem   1) 을 쓰면 설정이 .env 한 곳에만 남습니다.
rem  ==========================================================
setlocal enabledelayedexpansion
cd /d %~dp0
set PYTHONIOENCODING=utf-8
set PY=

if exist ".env" (
  for /f "usebackq tokens=1,* delims==" %%a in (".env") do (
    if /i "%%a"=="PYTHON" set PY=%%b
  )
)

if not defined PY if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
if not defined PY set PY=python

echo [python] !PY!
"!PY!" -m src.gui
if errorlevel 1 (
  echo.
  echo [실패] 위 메시지를 확인하세요.
  echo         패키지가 없으면:  "!PY!" -m pip install -r requirements.txt
  pause
)
