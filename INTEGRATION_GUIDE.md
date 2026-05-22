# Job Search API — Integration Guide

## Autenticação

```
Authorization: Bearer <API_KEY>
```

---

## Sincronizar perfil

```
POST <tunnel-url>/api/v1/users/sync
Content-Type: application/json; charset=utf-8
Authorization: Bearer <API_KEY>
```

**Resposta de sucesso:** HTTP 200 `{"status": "success", "message": "..."}`
**Erro de validação:** HTTP 422 com detalhe dos campos inválidos
**Retry em:** 5xx (a api pode estar temporariamente ocupada com scraping)

---

## Payload

```json
{
  "user_id": "25b5c883-2619-400a-aed0-53cc7de8dcab",
  "is_active": true,
  "job_profile": "tech",

  "job_titles": ["CTO", "Chief Technology Officer", "Head of Technology", "Head of Engineering"],
  "keywords": ["AI", "SaaS", "Cloud", "Product Strategy", "Automation"],
  "locations": ["Portugal"],
  "is_remote": true,
  "min_salary": 55000,
  "experience_levels": ["senior", "c-level", "director"],

  "negative_keywords": ["java", ".net", "frontend", "react", "backend", "fullstack", "qa", "tester", "intern"],
  "negative_companies": ["Canonical", "Dellent", "Randstad", "KWAN", "Adecco"],

  "contract_type": ["full-time"],
  "required_languages": ["portuguese", "english"],
  "search_description": "CTO com experiência em SaaS B2B e liderança de equipas de produto e engenharia. Procura posição executiva em Portugal ou remote EMEA.",

  "callback_url": "https://teu-supabase.supabase.co/functions/v1/receive-external-jobs"
}
```

---

## Campos — referência rápida

| Campo | Tipo | Para que serve |
|-------|------|----------------|
| `user_id` | string | **Obrigatório.** UUID do utilizador. |
| `job_titles` | string[] | **Obrigatório.** Títulos a pesquisar nos scrapers. |
| `keywords` | string[] | Palavras-chave do domínio. Melhoram o scoring. |
| `locations` | string[] | Localizações a pesquisar. Ex: `["Portugal", "Lisboa"]` |
| `is_remote` | boolean | Incluir vagas remote. |
| `min_salary` | integer | Salário mínimo anual em €. Penaliza vagas abaixo deste valor. |
| `experience_levels` | string[] | Seniority pretendida. Valores: `junior`, `mid-level`, `senior`, `lead`, `manager`, `director`, `c-level` |
| `negative_keywords` | string[] | ⭐ **NOVO.** Vagas com estas palavras no título são bloqueadas. |
| `negative_companies` | string[] | ⭐ **NOVO.** Empresas completamente excluídas (match parcial). |
| `contract_type` | string[] | ⭐ **NOVO.** Tipos aceites: `full-time`, `part-time`, `contract` |
| `required_languages` | string[] | ⭐ **NOVO.** Idiomas falados pelo utilizador. |
| `search_description` | string | ⭐ **NOVO.** Texto livre do perfil. Melhora significativamente o ranking das vagas. |
| `job_profile` | string | Define os scrapers activos. Ver `GET /api/v1/profiles`. Default: `tech` |
| `is_active` | boolean | `false` pausa as pesquisas sem apagar o perfil. |
| `callback_url` | string | URL para receber vagas via webhook. |

**Campos omitidos** preservam o valor anterior na base de dados.
**Arrays vazios** `[]` limpam o campo.

---

## Webhook recebido pela tua aplicação

O sistema envia um POST para a `callback_url` após cada run diário.

```
POST <callback_url>
X-Webhook-Secret: <EXTERNAL_WEBHOOK_SECRET>
```

**Verifica sempre o `X-Webhook-Secret`** antes de processar. Responde com HTTP 2xx para confirmar recepção.

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
      "link": "https://linkedin.com/jobs/view/...",
      "relevance_score": 100,
      "salario": "€80k–€120k",
      "tipo_contrato": "Full-Time",
      "nivel_experiencia": "C-Level",
      "data_scraped": "2026-05-22 01:15:00",
      "descricao_completa": "..."
    }
  ]
}
```

---

## Consultar vagas directamente (alternativa ao webhook)

```
GET <tunnel-url>/api/v1/jobs?user_id=<id>&sort_by_relevance=true&min_score=50&status=Ativa
Authorization: Bearer <API_KEY>
```

---

*searching-eng v2.1 — 2026-05-22*
