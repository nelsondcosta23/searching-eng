@echo off
title Verificar Vagas Expiradas
color 0E
echo =======================================================
echo    A iniciar VERIFICACAO DE VAGAS EXPIRADAS
echo =======================================================
echo.
echo O script vai aceder link a link as vagas ativas para confirmar
echo se ainda existem ou se ja expiraram. Este processo pode demorar.
echo Nao feches a janela.
echo.

python -u automation/job_verifier.py

echo.
echo =======================================================
echo    Verificacao Concluida com Sucesso! 
echo    Verifica o teu Dashboard para ver as alteracoes.
echo =======================================================
pause
