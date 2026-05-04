# Guia de Integração: API de Sincronização de Perfis de Pesquisa

Este documento detalha o funcionamento do endpoint da API responsável por sincronizar as preferências de pesquisa de emprego dos utilizadores com o sistema central de scraping.

## Visão Geral

- **Endpoint:** `/api/v1/users/sync`
- **Método HTTP:** `POST`
- **Content-Type:** `application/json`
- **Objetivo:** Inserir um novo perfil de pesquisa ou atualizar um já existente (UPSERT baseado no `user_id`).

## Autenticação

Todos os pedidos devem ser autenticados enviando uma API Key válida no cabeçalho (Header) do pedido HTTP:

```http
Authorization: Bearer A_VOSSA_API_KEY
```
*(Contacte o administrador do sistema para obter a API Key)*

---

## Estrutura do Payload (JSON)

O corpo do pedido (`body`) deve ser um objeto JSON que respeite o seguinte esquema:

| Campo | Tipo | Obrigatório | Descrição | Exemplo |
| :--- | :--- | :---: | :--- | :--- |
| `user_id` | `String` | **Sim** | Identificador único do utilizador (ex: ID da vossa BD ou Auth0). | `"usr_987654321"` |
| `is_active` | `Boolean` | Não | Define se o sistema deve procurar vagas para este utilizador ativamente. Padrão: `true`. | `true` |
| `job_titles` | `Array of Strings` | **Sim** | Lista de cargos que o utilizador procura. | `["Python Developer", "Data Scientist"]` |
| `locations` | `Array of Strings` | Não | Lista de localidades/cidades. Pode ser vazia. | `["Lisboa", "Porto"]` |
| `is_remote` | `Boolean` | Não | Se `true`, o sistema irá procurar prioritariamente ou exclusivamente vagas remotas. Padrão: `false`. | `true` |
| `min_salary` | `Integer` | Não | Expectativa salarial mínima anual/mensal em formato numérico. Padrão: `0`. | `35000` |
| `experience_levels` | `Array of Strings` | Não | Lista com os níveis de senioridade desejados. | `["Pleno", "Sénior"]` |
| `keywords` | `Array of Strings` | **Sim** | Palavras-chave exatas para filtragem rigorosa (strict matching). Usado pelos scrapers para aprovar ou rejeitar vagas. | `["React", "Next.js", "Frontend"]` |
| `negative_keywords` | `Array of Strings` | Não | Palavras que, se encontradas no título ou descrição, forçam a **rejeição imediata** da vaga. | `["Estágio", "Trainee", "WordPress"]` |
| `callback_url` | `String` | Não | URL do vosso servidor para receber vagas automaticamente via Webhook. Ver secção abaixo. | `"https://vosso-software.com/webhook/vagas"` |

---

## Diretrizes de Integração (Motor de Filtragem Estrita)

Para que o outro software tire o máximo partido da nossa arquitetura de Scraping de Alta Precisão (Sniper), tenham em atenção as seguintes regras de negócio ao construir o Payload:

1. **Rigor nas `keywords`:** O nosso motor rejeita *qualquer vaga* que não contenha pelo menos uma das palavras-chave (`keywords`) no título ou nos dados estruturados da plataforma. Passem apenas termos relevantes!
2. **Proteção Anti-Ruído (Palavras de 2 letras):** Para evitar que a palavra portuguesa "em" (como em *"Vaga em Lisboa"*) faça disparar a pesquisa de "EM" (Engineering Manager), o nosso sistema **ignora automaticamente** todas as palavras-chave com menos de 3 letras, **exceto** se pertencerem à nossa *Whitelist Profissional* (`IT, UX, UI, QA, HR, VP, AI, ML, BI, C#`).
3. **Cruzamento Geográfico:** O motor gera dezenas de micro-pesquisas combinando cada `job_title` com cada cidade na lista de `locations`. Quantas mais cidades e títulos enviarem, mais exaustiva (e demorada) será a pesquisa.

---

## Exemplos de Pedido

### Exemplo 1: cURL (Terminal)
```bash
curl -X POST "https://api.oseudominio.com/api/v1/users/sync" \
     -H "Authorization: Bearer A_VOSSA_API_KEY" \
     -H "Content-Type: application/json" \
     -d '{
           "user_id": "auth0|demo123",
           "is_active": true,
           "job_titles": ["React Developer", "Frontend Engineer"],
           "locations": ["Lisboa", "Remoto"],
           "is_remote": true,
           "min_salary": 40000,
           "experience_levels": ["Pleno", "Sénior"],
           "keywords": ["react", "frontend", "typescript", "ui"],
           "negative_keywords": ["estágio", "angular", "wordpress"],
           "callback_url": "https://vosso-software.com/webhook/vagas"
         }'
```

### Exemplo 2: JavaScript (Fetch API)
```javascript
const payload = {
  user_id: "auth0|demo123",
  is_active: true,
  job_titles: ["React Developer", "Frontend Engineer"],
  locations: ["Lisboa", "Remoto"],
  is_remote: true,
  min_salary: 40000,
  experience_levels: ["Pleno", "Sénior"],
  keywords: ["react", "frontend", "typescript", "ui"],
  negative_keywords: ["estágio", "angular", "wordpress"],
  callback_url: "https://vosso-software.com/webhook/vagas"
};

fetch('https://api.oseudominio.com/api/v1/users/sync', {
  method: 'POST',
  headers: {
    'Authorization': 'Bearer A_VOSSA_API_KEY',
    'Content-Type': 'application/json'
  },
  body: JSON.stringify(payload)
})
.then(response => response.json())
.then(data => console.log(data));
```

---

## Códigos de Resposta Esperados

### ✅ `200 OK` (Sucesso)
O perfil foi guardado ou atualizado com sucesso.
```json
{
  "status": "success",
  "message": "Profile for auth0|demo123 updated successfully."
}
```

### ❌ `401 Unauthorized` (Erro de Autenticação)
A API Key fornecida é inválida ou o cabeçalho `Authorization` não foi enviado.
```json
{
  "detail": "Invalid or missing API key."
}
```

### ❌ `422 Unprocessable Entity` (Erro de Validação)
O JSON enviado não cumpre a estrutura obrigatória (ex: faltam os `job_titles` ou o `user_id`). A API irá devolver um detalhe exato do campo que falhou.
```json
{
  "detail": [
    {
      "loc": ["body", "job_titles"],
      "msg": "field required",
      "type": "value_error.missing"
    }
  ]
}
```

---

## 🔔 Sistema de Webhook (Push de Resultados)

O sistema suporta notificações automáticas via **Webhook**. Ao registar um `callback_url` no perfil do utilizador, o sistema enviará automaticamente as vagas encontradas para o vosso servidor, **sem necessidade de polling**.

### Como funciona

```
Cronjob diário (meia-noite UTC)
   │
   ├── Scrapers recolhem vagas (LinkedIn, Sapo, Indeed, etc.)
   ├── Scorer calcula relevância (0–100)
   ├── Verifier marca vagas expiradas
   └── 🔔 Webhook Dispatcher
           └── Para cada utilizador com callback_url:
                  Seleciona Top 5 vagas do dia (maior relevance_score)
                  POST → callback_url com payload JSON completo
```

### Formato do Payload Recebido

O vosso endpoint receberá um `POST` com `Content-Type: application/json` no seguinte formato:

```json
{
  "event": "new_jobs_found",
  "user_id": "25b5c883-2619-400a-aed0-53cc7de8dcab",
  "scraped_at": "2026-05-03T23:05:11.432100",
  "total_sent": 5,
  "jobs": [
    {
      "id": 1423,
      "user_id": "25b5c883-2619-400a-aed0-53cc7de8dcab",
      "titulo": "Senior Software Engineer",
      "empresa": "Dellent",
      "localizacao": "Lisboa, Portugal",
      "plataforma": "LinkedIn PT",
      "categoria": "Tecnologia",
      "link": "https://www.linkedin.com/jobs/view/...",
      "data_publicacao": "2026-05-03",
      "data_scraped": "2026-05-03 23:01:44",
      "status": "Ativa",
      "descricao_completa": "Texto completo da vaga...",
      "recrutador_nome": "Ana Silva",
      "recrutador_link": "https://www.linkedin.com/in/ana-silva",
      "observacoes": null,
      "salario": "€55.000 - €70.000",
      "tipo_contrato": "Full-time",
      "nivel_experiencia": "Sénior",
      "relevance_score": 95
    }
  ]
}
```

### Campos do Payload — Dicionário Completo

| Campo | Tipo | Descrição |
| :--- | :--- | :--- |
| `event` | `String` | Sempre `"new_jobs_found"` — permite filtrar no vosso lado. |
| `user_id` | `String` | O ID do utilizador a quem pertencem as vagas. |
| `scraped_at` | `String (ISO 8601)` | Timestamp exato do envio. |
| `total_sent` | `Integer` | Número de vagas no payload (máximo 5). |
| `jobs[].id` | `Integer` | ID único da vaga na nossa base de dados. |
| `jobs[].titulo` | `String` | Título do cargo. |
| `jobs[].empresa` | `String` | Nome da empresa. |
| `jobs[].localizacao` | `String` | Localização da vaga. |
| `jobs[].plataforma` | `String` | Fonte (`"LinkedIn PT"`, `"Sapo Jobs"`, `"Indeed PT"`, etc.). |
| `jobs[].link` | `String` | URL direto para a vaga original. |
| `jobs[].status` | `String` | `"Ativa"` ou `"Expirada"`. |
| `jobs[].descricao_completa` | `String` | Descrição completa da vaga (pode ser longa). |
| `jobs[].salario` | `String` | Salário extraído (pode ser `null` se não disponível). |
| `jobs[].tipo_contrato` | `String` | Ex: `"Full-time"`, `"Part-time"`, `"Freelance"`. |
| `jobs[].nivel_experiencia` | `String` | Nível de senioridade extraído da plataforma. |
| `jobs[].relevance_score` | `Integer` | Pontuação de relevância calculada pelo nosso motor (0–100). |
| `jobs[].recrutador_nome` | `String` | Nome do recrutador (exclusivo do LinkedIn). Pode ser `null`. |
| `jobs[].recrutador_link` | `String` | Link do perfil do recrutador. Pode ser `null`. |

### Requisitos do vosso Endpoint de Webhook

O servidor externo deve:
- Aceitar pedidos `POST` com `Content-Type: application/json`.
- Responder com um código HTTP `2xx` (ex: `200 OK`) para confirmar receção.
- Processar o pedido em menos de **15 segundos** (o nosso sistema tem esse timeout).
- Se o vosso servidor estiver em baixo ou devolver um erro, o webhook **não será reenviado** (sem retry automático). As vagas continuarão sempre acessíveis via polling pelo endpoint `GET /api/v1/jobs`.

### 🛡️ Segurança (Webhook Secret)

Para garantir que o vosso servidor só aceita dados legítimos do nosso sistema, cada pedido de Webhook inclui o seguinte cabeçalho de segurança:

```http
X-Webhook-Secret: O_VOSSO_SECRET_AQUI
```

Devem verificar se o valor recebido no cabeçalho `X-Webhook-Secret` coincide com o valor configurado no vosso sistema.

> **Nota:** Se não registarem um `callback_url`, o sistema funciona normalmente — apenas não enviará notificações automáticas. Podem sempre consultar as vagas manualmente via `GET /api/v1/jobs?user_id=...`.
