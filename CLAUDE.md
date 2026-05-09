# CLAUDE.md — Project Guide

Plataforma automatizada de scraping/agregação de vagas (PT + internacional). Stack: Python 3.9 · FastAPI · Streamlit · SQLite (WAL) · Docker Compose · Selenium/undetected-chromedriver.

## Big picture

Quatro serviços orquestrados via `docker-compose.yml`:

| Serviço | Porta | Função |
|---|---|---|
| `python_scraper` | — | Worker com cron (scrapers + scoring + verifier + webhook). Container vivo via `tail -f /dev/null`. |
| `job_api` | 8080 | FastAPI REST para apps externas (auth via `API_KEY`). |
| `streamlit_app` | 8501 | Dashboard manual de filtragem. |
| `cloudflare_tunnel` | — | Expõe `job_api` publicamente. |

Pipeline diário (cron, [config/crontab](config/crontab)):
1. **00:00** — `orchestrator.py`: scrape → score → verify → webhook
2. **13:15** — `send_email.py` (Resend API)
3. **21:00** — `job_verifier.py` (re-marca expiradas)
4. **Domingo 03:00** — `clean_jobs.py` (purga >`DIAS_RETENCAO` dias, default 45)

## Componentes-chave

- [automation/orchestrator.py](automation/orchestrator.py) — coordena scrapers em **2 fases**: Net-Empregos (paralelo, RSS/HTTP) → Expresso/Sapo/Indeed/LinkedIn (sequencial, Selenium pesado).
- [automation/db_helper.py](automation/db_helper.py) — **única** porta de escrita de jobs. Tem retry com backoff para `database is locked`. Todos os scrapers DEVEM usar `save_job()` / `job_exists()`.
- [automation/profile_fetcher.py](automation/profile_fetcher.py) — gera search queries. Dois modos:
  - **API mode** (default): obtém perfil de uma Supabase Edge Function, cacheia em `tmp/profile_cache.json` (TTL 1h).
  - **Local mode**: se `TARGET_USER_ID` estiver no env, lê de `users_perfil` (SQLite local).
- [automation/job_scorer.py](automation/job_scorer.py) — TF-IDF caseiro (sem ML libs). Atribui `relevance_score` 0–100. Pesos: title×4, observacoes×2, descricao×1; phrase match dá bónus extra.
- [automation/job_verifier.py](automation/job_verifier.py) — verifica links 404 / mensagens "expirada". Indeed/LinkedIn vão por Selenium; resto por `requests.head()`.
- [automation/webhook_dispatcher.py](automation/webhook_dispatcher.py) — para cada user com `callback_url`, faz POST com top-N jobs do dia (`X-Webhook-Secret` header).
- [scrapers/linkedin_scraper.py](scrapers/linkedin_scraper.py) — **híbrido**: Guest API (rápido, sem Selenium) para listing + Selenium para deep extract.
- [scrapers/itjobs_scraper.py](scrapers/itjobs_scraper.py) — usa a API oficial JSON de itjobs.pt (auth via `ITJOBS_API_KEY`). Sem Selenium, paginação `limit`/`page`, sem campo de location na resposta (apenas `country` e `workModel`).
- [scrapers/companies_scraper.py](scrapers/companies_scraper.py) — scraper unificado para portais directos das empresas. Lê [config/companies.json](config/companies.json) e despacha por estratégia: **Ashby > Lever > Greenhouse > HTML**. As 3 ATS APIs são públicas, sem auth. Cada vaga vai para a BD com `plataforma="Companies: <Empresa> (<Strategy>)"` para se distinguirem na dashboard. Filtros de localização suportam regex `*_location_filter` por empresa, com defaults PT/Remote+EU e exclusão automática de regiões não-EU (Brazil, US, Philippines, etc.). HTML strategy é best-effort — muitos portais são SPAs que devolvem 0 jobs (aceitável).
- [scrapers/_shared.py](scrapers/_shared.py) — helpers partilhados (Fase G refactor): `negative_keyword_match()` com word-boundary (corrige bug em que "intern" bloqueava "international"), `make_session()` factory para HTTP com retries+headers. **Não unifica ainda** o Chrome driver factory nem cria `BaseScraper` — cada scraper Selenium tem tweaks subtis (UA rotation, lock cleanup) e o ROI vs. risco do refactor profundo não justifica enquanto tudo funciona.

## Schema da base de dados ([init_db.py](init_db.py))

Schema v6. Duas tabelas principais:

- `vagas` — jobs com `relevance_score`, `salario`, `tipo_contrato`, `nivel_experiencia`. UNIQUE em `link` e em `(plataforma, id_externo)`.
- `users_perfil` — preferências (`job_titles`, `keywords`, `negative_keywords`, `locations`, `callback_url`...) — campos lista são strings comma-separated.

`init_db.py` é **idempotente** — corre sempre no entrypoint e aplica `ALTER TABLE` se faltarem colunas (migrations inline). Nunca alteres o schema sem adicionar uma migration aqui.

## Convenções importantes

- **Línguas mistas**: schema/dominio em **português** (`vagas`, `titulo`, `empresa`, `localizacao`, `descricao_completa`, `recrutador_nome`); código/comentários em **inglês**. Mantém este split — não traduzas nomes de colunas.
- **`strict_keyword_match()`** em `profile_fetcher.py`: ignora keywords <3 chars **exceto** whitelist (`IT, UX, UI, QA, HR, VP, AI, ML, BI, C#`). Razão: a palavra portuguesa "em" não pode disparar match de "EM" (Engineering Manager). Não removas esta whitelist sem perceber o impacto.
- **SQLite WAL + retry**: toda a conexão usa `PRAGMA journal_mode=WAL` e timeout de 10–20s. Para escritas, usa o helper com retry (5 tentativas). Não escrevas direto à BD em código novo.
- **Logs**: tudo via `print()` capturado pelo cron para `logs/scraper.log` e `logs/verifier.log`. Não introduzas o módulo `logging` sem combinar primeiro — partir-se-ia o pipeline atual.
- **Selenium versionado**: alguns scrapers fazem pin a `version_main=147` em `uc.Chrome(...)`. Se Chrome do container actualizar, esse pin pode partir — verificar antes de mexer.
- **Env vars críticas** (ver `.env.example`): `API_KEY`, `RESEND_API_KEY`, `EXTERNAL_WEBHOOK_SECRET`, `TARGET_USER_ID` (opcional, força local mode), `MAX_JOBS_PER_PLATFORM` (0=ilimitado).

## Comandos comuns

```bash
# Build & arrancar tudo
docker-compose up -d --build

# Forçar scrape manual (Windows)
./Procurar_Vagas_Agora.bat
# ou diretamente:
docker exec python_scraper python /app/automation/orchestrator.py

# Re-score de todos os jobs (após mudar perfil)
docker exec python_scraper python /app/automation/job_scorer.py --rescore-all

# Verificar BD ad-hoc
docker exec python_scraper sqlite3 /app/database/vagas.db "SELECT plataforma, COUNT(*) FROM vagas GROUP BY plataforma"

# Logs ao vivo
docker logs -f python_scraper
```

## Cloudflare Tunnel

Actualmente em **Quick Tunnel mode** — gera um URL `*.trycloudflare.com` aleatório a cada restart. Para hostname permanente, migra para **Named Tunnel**:

1. `cloudflared tunnel login` (no host, abre browser para autenticar com a conta Cloudflare)
2. `cloudflared tunnel create searching-eng-api` → gera UUID e `~/.cloudflared/<UUID>.json`
3. Copia a credencial para `./tunnel/credentials.json` (versionado em `.gitignore`)
4. Cria `./tunnel/config.yml` apontando para `http://job_api:8080`
5. No Cloudflare dashboard → DNS → adicionar CNAME do hostname desejado para `<UUID>.cfargotunnel.com`
6. Substituir o serviço `cloudflare_tunnel` no `docker-compose.yml` por:
   ```yaml
   command: tunnel --config /etc/cloudflared/config.yml run searching-eng-api
   volumes:
     - ./tunnel:/etc/cloudflared:ro
   ```

## Gotchas
- **CORS `allow_origins=["*"]` + `allow_credentials=False`** ([api/main.py:46-52](api/main.py:46)) — esta combinação é a única válida pelo spec do CORS quando se usa wildcard. Bearer auth funciona sem credentials mode. NÃO mudar para `allow_credentials=True` sem listar origens específicas.
- **`profile_fetcher.py` faz HTTP ao Supabase em import-time indireto** (via `_get_strategy()`). Há fallback consistente para `users_perfil` local em todos os getters (Fase A); se o Supabase estiver down, todo o sistema continua a funcionar com o perfil local activo.
- **`SCRAPERS_PARALLEL` tem 3 items hoje** ([automation/orchestrator.py:16](automation/orchestrator.py:16)) — Net-Empregos, ITJobs, Companies. Sapo/Expresso continuam em SEQUENTIAL porque usam Selenium.
- **`ITJOBS_API_KEY`, `OWNER_USER_ID` e `EXTERNAL_WEBHOOK_SECRET`** são lidas só no arranque do container — alterações ao `.env` requerem `docker-compose up -d` ou `docker-compose restart <serviço>`.
- **Branch atual: worktree `claude/peaceful-vaughan-166658`** — main branch é `main`.
