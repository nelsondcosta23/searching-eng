# scrapper_tcc

Ferramenta local de inteligência de mercado de trabalho de tecnologia (PT + internacional).

---

## O que faz

A ferramenta faz scraping local on-demand de vagas de tecnologia a partir de múltiplas fontes, classifica as vagas em categorias tecnológicas, enriquece as informações das empresas consultando a Wikidata e fornece uma dashboard para análise.

**Fontes:**
- LinkedIn PT — Guest API para listagem + Playwright para detalhes
- ITJobs — API oficial (requer `ITJOBS_API_KEY`)
- Companies — Portais ATS diretos (Greenhouse, Lever, Ashby)
- Sapo Jobs — Playwright para extração
- Expresso Jobs — Playwright para extração (com bypass de WAF)
- Landing.jobs — API JSON pública
- Indeed PT — *Quebrado na fonte* (bloqueado por anti-bot no modo headless)

---

## Configuração e Inicialização

1. Copie o arquivo `.env.example` para `.env` e configure as variáveis necessárias (como `ITJOBS_API_KEY`).
2. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   playwright install chromium
   ```
3. Inicialize o banco de dados (SQLite):
   ```bash
   python init_db.py
   ```

---

## Como Usar

### Dashboard Streamlit
Inicie a interface gráfica para visualizar, filtrar as vagas, e disparar scrapers on-demand:
```bash
streamlit run app/job_dashboard.py
```

### Linha de Comando (CLI)
Para rodar todos os scrapers e o pós-processamento:
```bash
python automation/orchestrator.py
```

Para rodar apenas scrapers específicos:
```bash
python automation/orchestrator.py --scrapers itjobs,sapo
```

---

## Layout do Projeto

```
scrapper_tcc/
├── app/
│   └── job_dashboard.py       Interface Streamlit
├── automation/
│   ├── orchestrator.py        Orquestrador de execução
│   ├── db_helper.py           Helper thread-safe de escrita SQLite
│   ├── profile_fetcher.py     Carrega configurações do tech_profile.json
│   ├── job_classifier.py      Classificador baseado em tokens
│   ├── company_enricher.py    Cliente Wikidata fail-safe
│   └── job_analytics.py       Métricas de idade de vagas e rankings
├── scrapers/                  Módulos de scrapers individuais
├── config/
│   ├── companies.json         Configuração de ATS de empresas
│   └── tech_profile.json      Termos e exclusões de tecnologia
├── database/
│   └── vagas.db               Banco de dados local SQLite
├── init_db.py                 Inicializador de tabelas e migrations
├── requirements.txt           Dependências do projeto
└── pytest.ini                 Configuração do pytest
```

---

## Testes e Qualidade

Execute a suite de testes unitários:
```bash
python -m pytest tests/unit/
```
