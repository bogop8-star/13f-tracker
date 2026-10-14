@echo off
rem 작업 스케줄러에서 실행하는 용도. 결과는 reports\ 와 update.log 에 남습니다.
cd /d "%~dp0"
python tracker.py update >> update.log 2>&1
