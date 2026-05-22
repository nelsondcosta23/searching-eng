# Guia de Integração — Job Search API

Este documento descreve tudo o que o software externo precisa de saber para sincronizar perfis de utilizador com o sistema de scraping de vagas.

---

## URL base da API

A API é exposta via Cloudflare Tunnel. O URL muda cada vez que o servidor reinicia.
O URL actual chega ao teu software via webhook sempre que muda — campo `base_url` no evento `tunnel_updated`.

Para obter o URL actual:
```
GET https://<tunnel-url>/api/v1/status
```
Se responder `{"status": "ok"}`, o URL está activo.

---

## Autenticação

Todos os pedidos precisam do header:
```
Authorization: Bearer <API_KEY>
```

A `API_KEY` é partilhada fora deste documento (não colocar em código versionado).

---

## Sincronizar perfil de utilizador

### Request

```
POST <base_url>/api/v1/users/sync
Content-Type: application/json; charset=utf-8
Authorization: Bearer <API_KEY>
```

### Resposta de sucesso — HTTP 200

```json
{
  "status": "success",
  "message": "Profile for 25b5c883-... updated successfully."
}
```

### Respostas de erro

| Código | Situação | Exemplo de `detail` |
|--------|----------|---------------------|
| 401 | API key errada ou em falta | `"Invalid or missing API key."` |
| 403 | user_id não autorizado (OWNER_USER_ID configurado) | `"This API instance only serves user_id=..."` |
| 422 | Payload inválido (campo obrigatório em falta, tipo errado) | `[{"loc": ["body", "job_titles"], "msg": "field required"}]` |
| 500 | Erro interno (base de dados bloqueada, etc.) | `"database is locked"` |

Em caso de 500, podes fazer retry após 5 segundos.

---

## Payload — todos os campos

```json
{
  "user_id": "25b5c883-2619-400a-aed0-53cc7de8dcab",
  "is_active": true,

  "job_titles": [
    "Chief Technology Officer",
    "CTO",
    "Head of Technology",
    "Head of Engineering",
    "VP Engineering",
    "VP Technology"
  ],

  "keywords": [
    "AI",
    "SaaS",
    "Cloud",
    "Product Strategy",
    "Automation"
  ],

  "negative_keywords": [
    "java",
    ".net",
    "frontend",
    "react",
    "angular",
    "vue",
    "backend",
    "fullstack",
    "nodejs",
    "salesforce",
    "mulesoft",
    "citrix",
    "sharepoint",
    "sap",
    "qa",
    "tester",
    "data engineer",
    "data analyst",
    "devops engineer",
    "estagiário",
    "intern",
    "trainee",
    "freelance"
  ],

  "negative_companies": [
    "Canonical",
    "Dellent",
    "Randstad",
    "KWAN",
    "Adecco",
    "Manpower",
    "Michael Page"
  ],

  "locations": ["Portugal"],
  "is_remote": true,
  "min_salary": 55000,

  "experience_levels": ["senior", "c-level", "director"],

  "contract_type": ["full-time"],

  "required_languages": ["portuguese", "english"],

  "search_description": "CTO / Head of Technology com mais de 10 anos de experiência em liderança de equipas de produto e engenharia. Especializado em arquitecturas SaaS B2B, estratégia de produto digital, e transformação tecnológica. Procura posições de liderança executiva em startups Series A/B ou scale-ups em Portugal ou remote EMEA. Background em cloud (AWS/GCP), automação de processos, e desenvolvimento de roadmaps de produto.",

  "job_profile": "tech",

  "callback_url": "https://teu-supabase.supabase.co/functions/v1/receive-external-jobs"
}
```

---

## Descrição de cada campo

### Obrigatórios

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `user_id` | `string` | UUID do utilizador. Deve ser o mesmo em todos os syncs. |
| `job_titles` | `string[]` | Títulos de cargo a pesquisar. Mais específico = menos ruído. |

### Filtragem de vagas — impacto directo na qualidade

| Campo | Tipo | Como é usado |
|-------|------|-------------|
| `negative_keywords` | `string[]` | Vagas cujo **título** contenha qualquer uma destas palavras são ignoradas. Ex: `"java"` bloqueia "Java Tech Lead". Match por palavra inteira (não bloqueia "javascript" com "java"). |
| `negative_companies` | `string[]` | Empresas a excluir completamente. Match parcial, case-insensitive. Ex: `"Canonical"` bloqueia todos os jobs da Canonical em todos os scrapers. |
| `contract_type` | `string[]` | Tipos de contrato pretendidos: `"full-time"`, `"part-time"`, `"contract"`. Se omitido, aceita todos os tipos. |
| `required_languages` | `string[]` | Idiomas que o utilizador fala: `"portuguese"`, `"english"`, `"spanish"`, etc. |

### Scoring — melhoram a ordenação dos resultados (0–100)

| Campo | Tipo | Como é usado |
|-------|------|-------------|
| `search_description` | `string` | Texto livre sobre o perfil e o que procura. Usado directamente pelo scorer TF-IDF. Quanto mais rico e específico, melhor o ranking das vagas. |
| `keywords` | `string[]` | Palavras-chave do domínio profissional. Usadas no scoring e em queries de pesquisa enriquecidas. |
| `experience_levels` | `string[]` | Seniority pretendida. Valores aceites: `"junior"`, `"mid-level"`, `"senior"`, `"lead"`, `"manager"`, `"director"`, `"c-level"`. Vagas com match no nível correcto recebem bónus de +8 pontos. |
| `min_salary` | `integer` | Salário mínimo anual em euros. Vagas com salário declarado abaixo deste valor recebem penalidade de −10 a −25 pontos. Vagas sem salário declarado não são afectadas. |

### Logística

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `locations` | `string[]` | Localizações a pesquisar. Ex: `["Portugal"]`, `["Lisboa", "Porto"]`. |
| `is_remote` | `boolean` | `true` para incluir vagas remote. |
| `is_active` | `boolean` | `false` pausa todas as pesquisas (perfil é preservado na base de dados). |
| `job_profile` | `string` | Define qual conjunto de scrapers é usado. Ver secção abaixo. |
| `callback_url` | `string` | URL para receber novas vagas via webhook após cada run. |

---

## Valores válidos para `job_profile`

Podes obter a lista actualizada em:
```
GET <base_url>/api/v1/profiles
Authorization: Bearer <API_KEY>
```

Valores disponíveis actualmente:
```
tech, data, design, engineering, marketing, hr, finance, sales,
legal, hospitality, healthcare, education, logistics, construction,
operations, retail, manufacturing, customer_service, admin, generalist
```

Se enviares um valor desconhecido, o sistema usa `"generalist"` como fallback.
Se omitires o campo, o valor existente na base de dados é preservado.

---

## Regras de actualização parcial

| O que enviares | O que acontece |
|----------------|---------------|
| Campo com valor (`"job_titles": [...]`) | Valor é actualizado |
| Campo omitido (não incluído no JSON) | Valor anterior é **preservado** |
| Campo com array vazio (`"negative_keywords": []`) | Campo é **limpo** (apaga o valor anterior) |
| `"job_profile": null` ou campo omitido | Valor anterior é preservado |

---

## Payload mínimo recomendado

Se só consegues enviar alguns campos, por esta ordem de prioridade:

```json
{
  "user_id": "25b5c883-2619-400a-aed0-53cc7de8dcab",
  "job_titles": ["CTO", "Chief Technology Officer", "Head of Technology", "Head of Engineering"],
  "negative_keywords": ["java", ".net", "frontend", "react", "angular", "backend", "fullstack", "qa", "tester", "intern"],
  "negative_companies": ["Canonical", "Dellent", "Randstad", "KWAN", "Adecco"],
  "search_description": "CTO com experiência em SaaS B2B, liderança de equipas de produto e engenharia em Portugal ou remote EMEA.",
  "job_profile": "tech",
  "locations": ["Portugal"],
  "is_remote": true,
  "min_salary": 55000
}
```

---

## Webhook — vagas enviadas após cada run

Após cada run diário (normalmente de madrugada), o sistema envia um POST para a `callback_url` com as melhores vagas encontradas.

### Request enviado pelo sistema

```
POST <callback_url>
Content-Type: application/json; charset=utf-8
X-Webhook-Secret: <EXTERNAL_WEBHOOK_SECRET>
User-Agent: SearchingEng-WebhookDispatcher/1.0
```

**O teu software deve verificar o header `X-Webhook-Secret`** antes de processar o payload. O valor é partilhado fora deste documento. Se o header estiver ausente ou errado, rejeita o pedido com HTTP 401.

### Payload recebido

```json
{
  "event": "new_jobs_found",
  "user_id": "25b5c883-...",
  "scraped_at": "2026-05-22T01:00:00",
  "total_sent": 5,
  "jobs": [
    {
      "id": 4923,
      "titulo": "CTO",
      "empresa": "Critical Software",
      "localizacao": "Coimbra",
      "plataforma": "LinkedIn PT (Selenium)",
      "link": "https://linkedin.com/jobs/view/...",
      "relevance_score": 100,
      "salario": "€80k–€120k",
      "tipo_contrato": "Full-Time",
      "nivel_experiencia": "C-Level",
      "observacoes": "Seniority level: Director | Employment type: Full-time",
      "data_publicacao": "2026-05-21",
      "data_scraped": "2026-05-22 01:15:00",
      "status": "Ativa",
      "descricao_completa": "...",
      "recrutador_nome": "João Silva",
      "recrutador_link": "https://linkedin.com/in/joaosilva"
    }
  ]
}
```

### Campos do job no webhook

| Campo | Tipo | Notas |
|-------|------|-------|
| `id` | `integer` | ID interno da base de dados |
| `titulo` | `string` | Título da vaga (UTF-8) |
| `empresa` | `string` | Nome da empresa |
| `localizacao` | `string` | Ex: `"Lisboa (Híbrido)"`, `"Portugal (Remoto)"` |
| `plataforma` | `string` | Fonte: `"LinkedIn PT"`, `"ITJobs"`, `"Companies: Feedzai (Ashby)"`, etc. |
| `link` | `string` | URL directo para a vaga |
| `relevance_score` | `integer` | 0–100. Quanto mais alto, melhor o match com o perfil. |
| `salario` | `string\|null` | Ex: `"€60.000–€80.000"`. `null` quando não declarado. |
| `tipo_contrato` | `string\|null` | Ex: `"Full-Time"`, `"Part-Time"`. `null` quando não disponível. |
| `nivel_experiencia` | `string\|null` | Ex: `"Sénior"`, `"C-Level"`, `"Lead"`. `null` quando não detectado. |
| `data_scraped` | `string` | Formato `"YYYY-MM-DD HH:MM:SS"`, sem timezone (UTC). |
| `descricao_completa` | `string\|null` | Texto completo da vaga. Truncado a 2000 chars no webhook. |

### Resposta esperada do teu software

O sistema espera HTTP 2xx. Em caso de erro 5xx ou timeout, faz até 3 retries com backoff exponencial (1s, 4s, 16s). Em caso de 4xx, não faz retry (considera erro permanente).

---

## Consultar vagas via API (alternativa ao webhook)

Em vez de esperar pelo webhook, podes também pedir as vagas directamente:

```
GET <base_url>/api/v1/jobs?user_id=<user_id>&run_date=all&sort_by_relevance=true&min_score=50
Authorization: Bearer <API_KEY>
```

Parâmetros úteis:
- `run_date=all` — todas as vagas | `run_date=2026-05-22` — só esse dia | omitido = hoje
- `sort_by_relevance=true` — ordena por score descendente
- `min_score=50` — só vagas com score ≥ 50
- `status=Ativa` — só vagas activas
- `limit=100` — máximo 1000, default 500

---

*searching-eng v2.1 — 2026-05-22*
