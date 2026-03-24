@echo off
title Forcar Procura de Vagas API
color 0B
echo =======================================================
echo    A iniciar PROCURA MANUAL de Vagas de Topo (API V2)
echo =======================================================
echo.
echo As frotas dos scrapers (LinkedIn, Indeed, Sapo, etc.) estao a caminho...
echo Este processo pode demorar alguns minutos. Nao feches a janela.
echo.

docker exec python_scraper python /app/automation/orchestrator.py

echo.
echo =======================================================
echo    Procura Concluida com Sucesso! 
echo    Podes ir dar refresh ao teu Dashboard (localhost:8501)
echo =======================================================
pause
