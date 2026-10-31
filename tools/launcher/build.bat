@echo off
rem Builds Moonlight.exe in the repo root with the C# compiler that ships
rem with Windows (.NET Framework 4). Run it once, or after Moonlight.cs changes.
cd /d "%~dp0"
"%WINDIR%\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /target:winexe /out:..\..\Moonlight.exe Moonlight.cs
if errorlevel 1 pause
