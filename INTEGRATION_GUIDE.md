# Guia de Integração — Job Search API

Este documento descreve o que o software externo (ex: Supabase/Trakki) precisa de enviar para que o sistema de scraping encontre vagas com a maior qualidade de match possível.

---

## Autenticação

Todos os pedidos precisam do header:
```
Authorization: Bearer <API_KEY>
```

A `API_KEY` é a mesma definida no `.env` do servidor.

---

## Endpoint de sincronização de perfil

```
POST /api/v1/users/sync
Content-Type: application/json
Authorization: Bearer <API_KEY>
```

Este endpoint actualiza o perfil do utilizador. O sistema usa estes dados para:
1. **Gerar queries de pesquisa** nos scrapers (job_titles + locations)
2. **Filtrar vagas irrelevantes** (negative_keywords + negative_companies)
3. **Calcular relevância** das vagas encontradas (search_description + keywords)
4. **Enviar resultados** para o teu software (callback_url)

---

## Payload completo

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

  "callback_url": "https://teu-supabase.supabase.co/functions/v1/receive-external-jobs?secret=..."
}
```

---

## Descrição de cada campo

### Campos obrigatórios

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `user_id` | `string` | UUID único do utilizador |
| `job_titles` | `string[]` | Títulos de cargo a pesquisar. **Mais específico = menos ruído.** |

### Campos de filtragem de vagas — impacto directo na qualidade

| Campo | Tipo | Como é usado |
|-------|------|-------------|
| `negative_keywords` | `string[]` | Vagas cujo **título** contenha qualquer uma destas palavras são **ignoradas** pelo scraper. Ex: `"java"` elimina "Java Tech Lead". |
| `negative_companies` | `string[]` | ⭐ **NOVO** — Empresas a excluir completamente. Match parcial, case-insensitive. Ex: `"Canonical"` elimina todos os jobs da Canonical. |
| `contract_type` | `string[]` | ⭐ **NOVO** — Tipos de contrato pretendidos. Valores: `"full-time"`, `"part-time"`, `"contract"`. |
| `required_languages` | `string[]` | ⭐ **NOVO** — Idiomas que o utilizador fala. Usado para filtrar vagas que exigem idiomas não listados. |

### Campos de scoring — melhoram a ordenação dos resultados

| Campo | Tipo | Como é usado |
|-------|------|-------------|
| `search_description` | `string` | ⭐ **NOVO** — Texto livre sobre o perfil profissional e o que procura. O sistema de scoring (TF-IDF) usa este texto para calcular relevância. **Quanto mais rico, melhor o match.** |
| `keywords` | `string[]` | Palavras-chave do domínio profissional. Usadas no scoring e na geração de queries enriquecidas. |
| `experience_levels` | `string[]` | Seniority pretendida. Valores: `"junior"`, `"mid-level"`, `"senior"`, `"lead"`, `"manager"`, `"director"`, `"c-level"`. Vagas com match recebem +8 pontos de relevância. |
| `min_salary` | `integer` | Salário mínimo anual (€). Vagas que declaram salário abaixo deste valor recebem penalidade. |

### Campos de logística

| Campo | Tipo | Descrição |
|-------|------|-----------|
| `locations` | `string[]` | Localizações a pesquisar. Ex: `["Portugal", "Lisboa", "Porto"]` |
| `is_remote` | `boolean` | `true` para incluir vagas remote-first |
| `is_active` | `boolean` | `false` para pausar todas as pesquisas deste utilizador |
| `job_profile` | `string` | Define qual conjunto de scrapers é usado. Ver `GET /api/v1/profiles` para lista completa. Default: `"tech"` |
| `callback_url` | `string` | URL para receber as novas vagas via webhook (POST) após cada run diário |

---

## Comportamento de actualização parcial

Se um campo não for enviado no payload, o valor existente na base de dados é **preservado**.

Excepção: `job_profile` — se não enviado, preserva o valor anterior.

---

## Impacto esperado ao enviar `negative_companies` e `search_description`

### Antes (perfil actual):
- 339 vagas na DB, ~80 da Canonical irrelevantes
- "Head of Technology" @ Blip score = 38
- "Marketing Manager" @ Canonical score = 93

### Depois (com perfil completo):
- Canonical completamente excluída → -80 vagas irrelevantes
- `search_description` rica → scorer tem mais vocabulário → melhor discriminação
- `negative_keywords` activos → Java/Frontend Tech Leads filtrados na entrada
- Vagas genuínas (CTO, Head of Engineering) sobem nos scores

---

## Exemplo mínimo (só os campos mais impactantes)

Se não consegues enviar o payload completo, pelo menos envia estes:

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

## Verificar o perfil actual

```
GET /api/v1/users/sync
```

Não existe um GET directo ao perfil. Para verificar o que está guardado, usa a BD directamente ou consulta os campos que chegam no webhook.

---

## Webhook — formato de resposta

Após cada run diário, o sistema envia para a `callback_url` um POST com:

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
      "plataforma": "LinkedIn PT",
      "link": "https://linkedin.com/jobs/...",
      "relevance_score": 100,
      "salario": "€80k–€120k",
      "tipo_contrato": "Full-Time",
      "nivel_experiencia": "C-Level",
      "data_scraped": "2026-05-22 01:15:00"
    }
  ]
}
```

Header de autenticação do webhook:
```
X-Webhook-Secret: <EXTERNAL_WEBHOOK_SECRET>
```

---

*Gerado em 2026-05-22 — searching-eng v2.1*
