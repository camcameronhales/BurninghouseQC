@echo off
REM Burninghouse QC — Windows launcher.
REM Edit INSTALL_DIR if you put the app somewhere else, then use this file as
REM the target for either NSSM or Task Scheduler (see docs/service-setup.md).

set INSTALL_DIR=C:\BurninghouseQC
set CONFIG=%INSTALL_DIR%\config.toml

cd /d "%INSTALL_DIR%"
"%INSTALL_DIR%\.venv\Scripts\python.exe" -m burninghouse_qc.cli -c "%CONFIG%" watch
