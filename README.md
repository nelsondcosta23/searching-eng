# Tech Job Market — PT, UK & USA

Uma ferramenta de inteligência de mercado de trabalho tecnológico concebida para a recolha, classificação e enriquecimento de vagas de emprego em **Portugal (PT), Reino Unido (UK) e Estados Unidos da América (USA)**. Os dados são recolhidos e segmentados por país (Portugal, Reino Unido e Estados Unidos), permitindo comparar a actividade de contratação entre mercados.

Este projeto foi desenvolvido como um sistema de suporte analítico para Trabalho de Conclusão de Curso (TCC), focando-se na distribuição geográfica, regimes de trabalho, maturidade das empresas contratantes e procura por competências tecnológicas.

---

## 🚀 Funcionalidades Principais

*   **7 Scrapers Integrados:**
    *   `LinkedIn PT`: Execução híbrida (Guest API rápida + Playwright headless para detalhe).
    *   `ITJobs`: Integração com a API oficial em Portugal.
    *   `Companies`: Monitorização de portais ATS diretos (Greenhouse, Lever, Ashby) de 224 empresas tecnológicas.
    *   `Sapo Jobs` e `Expresso Jobs`: Extração com bypass de WAF através de Playwright com emulação de display virtual (Xvfb).
    *   `Landing.jobs`: API JSON pública.
    *   `Indeed PT`: Mapeamento sequencial com limite de profundidade e abortagem inteligente sob bloqueio.
*   **Classificação Inteligente:** Classificação automática de vagas baseada em correspondência de termos estruturados (Backend, Frontend, Fullstack, DevOps/Cloud, Data/ML, Mobile, QA, Security e IT/Infra), filtrando posições não-tecnológicas.
*   **Enriquecimento com Wikidata:** Consulta assíncrona fail-safe à API da Wikidata para caching do ano de fundação e idade das empresas empregadoras.
*   **Enriquecimento de Metadados:** Extração automatizada de Regime de Trabalho (Remote/Hybrid/On-site), Senioridade (Júnior a C-Level), Competências Tecnológicas (40+ stacks catalogadas) e Salários.
*   **Dashboard Streamlit:** Interface gráfica moderna com métricas de repartição de mercado (Empregadores Diretos vs. Consultoras vs. Agências), tabelas de rankings setoriais (mapeamento via `config/company_sectors.json`), filtros avançados de exploração e exportação de dados para CSV.

---

## 🛠️ Configuração e Instalação

### Requisitos Prévios
*   Docker e Docker Compose instalados.

### Passos de Configuração
1.  Clone o repositório localmente.
2.  Copie o ficheiro de configuração de ambiente:
    ```bash
    cp .env.example .env
    ```
3.  Edite o ficheiro `.env` e configure as suas variáveis:
    *   `ITJOBS_API_KEY`: A sua chave de API obtida em [ITJobs](https://www.itjobs.pt/api).
    *   `MAX_JOBS_PER_PLATFORM`: Limite de vagas a guardar por scraper (defina `0` para ilimitado).

---

## 🐳 Execução via Docker (Recomendado)

O projeto está totalmente contentorizado, isolando as dependências do Chromium/Playwright e a base de dados SQLite.

### 1. Iniciar o Dashboard Streamlit
Construa as imagens e inicie a interface gráfica em background:
```bash
docker compose up --build -d dashboard
```
O painel ficará acessível no seu navegador em: `http://127.0.0.1:8502`.

### 2. Disparar a Recolha de Vagas (Scrapers)
Para executar a recolha completa de vagas e o pós-processamento de enriquecimento de dados:
```bash
docker compose run --rm scraper
```
*(Nota: O scraper corre os scripts na ordem recomendada, atualiza a base de dados `database/vagas.db` partilhada com o contentor do dashboard, e termina automaticamente).*

---

## 🔬 Execução Local para Desenvolvimento

Caso prefira correr fora de Docker, instale os requisitos no seu ambiente Python (3.10+):

```bash
pip install -r requirements.txt
playwright install chromium
python init_db.py
```

*   Para correr o dashboard: `streamlit run app/job_dashboard.py --server.port=8502`
*   Para rodar a suite de testes unitários: `pytest`

---

## ⚠️ Limitações Conhecidas do Sistema

Como qualquer ferramenta de recolha de dados baseada em recursos de terceiros, existem limitações intrínsecas:
1.  **Indeed PT:** Sujeito a bloqueios agressivos de Cloudflare na listagem. O scraper foi desenhado para abortar preventivamente caso receba múltiplos resultados vazios seguidos, evitando desperdício de CPU.
2.  **Wikidata API Coverage:** A cobertura de idades de empresas situa-se em cerca de 13% devido à ausência de registo de pequenas agências/consultoras locais na Wikidata. Estas empresas caem graciosamente no estado "Desconhecido".
3.  **Dependência do DOM:** Alterações estruturais nos portais Sapo, Expresso e Indeed podem quebrar os seletores do BeautifulSoup. A arquitetura centraliza estas lógicas em seletores bem isolados para simplificar a manutenção.

---

## 📄 Licença

Este projeto está licensed sob a Licença MIT. Consulte o ficheiro `LICENSE` para obter mais detalhes.
