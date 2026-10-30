@echo off
setlocal
cd /d "%~dp0"

where docker >nul 2>nul
if errorlevel 1 (
    echo Error: docker not found. Please install Docker Desktop and start it.
    exit /b 1
)

docker compose version >nul 2>nul
if errorlevel 1 (
    echo Error: Docker Compose v2 required ^(docker compose^). Please upgrade Docker Desktop.
    exit /b 1
)

if not exist "sources.local.json" (
    echo sources.local.json not found, creating from sources.json template...
    copy /Y "sources.json" "sources.local.json" >nul
    if errorlevel 1 (
        echo Error: failed to create sources.local.json from sources.json.
        exit /b 1
    )
    echo Please edit sources.local.json with your sites before crawling.
)

if not exist "data" mkdir "data"

echo Building image...
docker compose build
if errorlevel 1 exit /b 1

echo Initializing database...
docker compose run --rm crawler init
if errorlevel 1 exit /b 1

echo Running smoke test...
docker compose run --rm crawler smoke
if errorlevel 1 exit /b 1

echo Running API smoke test...
docker compose run --rm --entrypoint python crawler -X utf8 api_smoke.py
if errorlevel 1 exit /b 1

echo.
echo Docker deployment completed. Common commands:
echo   Crawl:      docker compose run --rm crawler run --config sources.local.json --days 3
echo   Test parse: docker compose run --rm crawler test --config sources.local.json --name ^<site^>
echo   Shell:      docker compose run --rm crawler shell
echo   Start API:  docker compose up -d api
echo   API docs:   http://localhost:8000/docs
echo   Database:   .\data\news.db
endlocal
