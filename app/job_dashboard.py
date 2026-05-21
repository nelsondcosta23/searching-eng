import streamlit as st
import sqlite3
import contextlib
import pandas as pd
import os
import psutil
from datetime import datetime, timedelta

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
LOCK_FILE = os.path.join(os.path.dirname(DB_PATH), 'orchestrator.lock')


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _relative_date(date_str: str) -> str:
    """'2026-05-21 15:29:50' → 'hoje 15:29' / 'ontem' / 'há 3d'"""
    if not date_str:
        return ''
    try:
        dt = datetime.strptime(date_str[:19], '%Y-%m-%d %H:%M:%S')
        now = datetime.now()
        diff = now - dt
        if diff.days == 0:
            return f"hoje {dt.strftime('%H:%M')}"
        if diff.days == 1:
            return f"ontem {dt.strftime('%H:%M')}"
        if diff.days < 7:
            return f"há {diff.days}d"
        return dt.strftime('%d %b')
    except Exception:
        return date_str[:10]


def _score_label(score: int) -> str:
    """Returns score with a coloured emoji indicator."""
    if score >= 60:
        return f"🟢 {score}"
    if score >= 30:
        return f"🟡 {score}"
    return f"🔴 {score}"


def is_job_running():
    if os.path.exists(LOCK_FILE):
        return True
    try:
        scripts = [
            "orchestrator.py", "job_verifier.py",
            "sapo_scraper.py", "expresso_scraper.py", "linkedin_scraper.py",
            "indeed_scraper.py", "itjobs_scraper.py", "companies_scraper.py",
        ]
        for proc in psutil.process_iter(['cmdline']):
            cmdline = proc.info.get('cmdline')
            if cmdline and any(s in " ".join(cmdline) for s in scripts):
                return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(page_title="Job Search Dashboard", page_icon="🔍", layout="wide")

import subprocess


# ─────────────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_jobs():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    with contextlib.closing(sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)) as conn:
        conn.execute('PRAGMA journal_mode=WAL;')
        return pd.read_sql_query('''
            SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link, status,
                   salario, tipo_contrato, nivel_experiencia, observacoes,
                   recrutador_nome, recrutador_link, data_publicacao,
                   COALESCE(relevance_score, 0) AS relevance_score,
                   COALESCE(data_scraped, '') AS data_scraped
            FROM vagas
            WHERE data_scraped >= date('now', '-45 days')
            ORDER BY COALESCE(relevance_score, 0) DESC, data_scraped DESC
        ''', conn)


@st.cache_data(ttl=300)
def load_job_description(job_id: int) -> str:
    if not os.path.exists(DB_PATH):
        return 'Sem descrição disponível.'
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            row = conn.execute("SELECT descricao_completa FROM vagas WHERE id = ?", (job_id,)).fetchone()
        return row[0] if row and row[0] else 'Sem descrição disponível.'
    except Exception:
        return 'Sem descrição disponível.'


def get_users_perfil():
    if not os.path.exists(DB_PATH):
        return []
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=5)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            return conn.execute(
                "SELECT user_id, job_titles FROM users_perfil WHERE is_active = 1 ORDER BY created_at"
            ).fetchall()
    except Exception:
        return []


def get_platform_metrics():
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=5)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            rows = conn.execute(
                "SELECT plataforma, status, COUNT(*) FROM vagas GROUP BY plataforma, status"
            ).fetchall()
        metrics = {}
        platforms = ['Sapo Jobs', 'Expresso Jobs', 'LinkedIn PT', 'Indeed PT', 'ITJobs', 'Companies']
        for p in platforms:
            key = p.split()[0].lower()
            ativas    = sum(c for plat, st, c in rows if key in plat.lower() and st == 'Ativa')
            expiradas = sum(c for plat, st, c in rows if key in plat.lower() and st == 'Expirada')
            metrics[p] = {'ativas': ativas, 'expiradas': expiradas}
        return metrics
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Terminal dialog
# ─────────────────────────────────────────────────────────────────────────────
@st.dialog("Terminal de Execução", width="large")
def run_in_terminal(cmd_list, env_vars=None, title="A executar...", post_cmd_list=None):
    st.write(f"**{title}**")

    initial_stats     = get_platform_metrics()
    metrics_placeholder = st.empty()

    def update_metrics():
        current_stats = get_platform_metrics()
        if not current_stats or not initial_stats:
            return
        with metrics_placeholder.container():
            cols = st.columns(6)
            platforms = ['Sapo Jobs', 'Expresso Jobs', 'LinkedIn PT', 'Indeed PT', 'ITJobs', 'Companies']
            for i, p in enumerate(platforms):
                short_name = p.replace(" PT", "").replace(" Jobs", "")
                curr = current_stats.get(p, {'ativas': 0, 'expiradas': 0})
                init = initial_stats.get(p, {'ativas': 0, 'expiradas': 0})
                diff_active = curr['ativas'] - init['ativas']
                diff_exp    = curr['expiradas'] - init['expiradas']
                active_delta = f"<div style='color:#10b981;font-size:11px;margin-top:-5px;'>+{diff_active} novas</div>" if diff_active > 0 else ""
                exp_delta    = f"<div style='color:#ef4444;font-size:11px;margin-top:-5px;'>+{diff_exp} exp.</div>"    if diff_exp    > 0 else ""
                with cols[i]:
                    st.markdown(f"""
<div style='background:#fdfdfd;padding:12px 5px;border-radius:10px;border:1px solid #eee;text-align:center;box-shadow:0 2px 4px rgba(0,0,0,.02);'>
  <div style='color:#666;font-size:12px;font-weight:600;text-transform:uppercase;margin-bottom:5px;'>{short_name}</div>
  <div style='color:#10b981;font-size:16px;font-weight:700;'>{curr['ativas']} <span style='font-size:10px;font-weight:400;'>Ativas</span></div>
  {active_delta}
  <div style='color:#ef4444;font-size:14px;font-weight:600;margin-top:5px;'>{curr['expiradas']} <span style='font-size:10px;font-weight:400;'>Exp.</span></div>
  {exp_delta}
</div>""", unsafe_allow_html=True)

    update_metrics()
    st.markdown("<br>", unsafe_allow_html=True)

    log_content = ""
    with st.container(height=350):
        log_box = st.empty()

    process = subprocess.Popen(
        cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, env=env_vars, bufsize=1,
    )
    loop_counter = 0
    for line in process.stdout:
        log_content += line
        log_box.code("\n".join(log_content.splitlines()[-150:]), language="bash")
        loop_counter += 1
        if loop_counter % 20 == 0:
            update_metrics()
    process.wait()
    update_metrics()

    if post_cmd_list:
        st.write("**A calcular relevância das vagas (Scorer)...**")
        with st.container(height=200):
            post_log_box = st.empty()
        post_process = subprocess.Popen(
            post_cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env_vars, bufsize=1,
        )
        post_log_content = ""
        for line in post_process.stdout:
            post_log_content += line
            post_log_box.code("\n".join(post_log_content.splitlines()[-100:]), language="bash")
        post_process.wait()

    st.success("Operação concluída com sucesso!")
    if st.button("Fechar e Atualizar o Dashboard", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.query_params.clear()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
df = load_jobs()

with st.sidebar:
    st.header("⚡ Ações Rápidas")
    st.caption("Podes forçar atualizações diretamente por aqui.")

    def sync_platform_filter():
        target = st.session_state.sidebar_platform
        if target == "Todas as Plataformas":
            st.session_state.main_platform = "Todas"
            return
        if target == "Companies":
            st.session_state.main_platform = "Companies"
            return
        base_name = target.split(' ')[0]
        for p in df['plataforma'].unique().tolist():
            if base_name.lower() in p.lower():
                st.session_state.main_platform = p
                return
        st.session_state.main_platform = "Todas"

    st.subheader("Procurar Novas Vagas")

    all_users = get_users_perfil()
    user_options = {"🌐 Todos os Utilizadores": "ALL"}
    for uid, titles in all_users:
        display = (titles or "").split(',')[0].strip()[:30] or str(uid)[:8]
        user_options[f"👤 {display}"] = uid

    selected_user_label = st.selectbox("Utilizador", list(user_options.keys()), key="sidebar_user")
    selected_user_id    = user_options[selected_user_label]

    scrape_target = st.selectbox("Plataforma", [
        "Todas as Plataformas", "Sapo Jobs", "Expresso Jobs",
        "LinkedIn PT", "Indeed PT", "ITJobs", "Companies",
    ], key="sidebar_platform", on_change=sync_platform_filter)

    max_jobs_limit = st.selectbox(
        "Limite de Vagas",
        options=[0, 5, 10, 20, 50, 100],
        index=0,
        format_func=lambda x: "Sem limite (produção)" if x == 0 else str(x),
    )

    if st.button("Iniciar Pesquisa", type="primary", use_container_width=True):
        env_with_limit = os.environ.copy()
        env_with_limit["MAX_JOBS_PER_PLATFORM"] = str(max_jobs_limit)
        env_with_limit["PYTHONUNBUFFERED"] = "1"
        if selected_user_id != "ALL":
            env_with_limit["TARGET_USER_ID"] = selected_user_id
        elif "TARGET_USER_ID" in env_with_limit:
            del env_with_limit["TARGET_USER_ID"]

        cmd_map = {
            "Todas as Plataformas": ["python", "-u", "/app/automation/orchestrator.py"],
            "Sapo Jobs":            ["python", "-u", "/app/scrapers/sapo_scraper.py"],
            "Expresso Jobs":        ["python", "-u", "/app/scrapers/expresso_scraper.py"],
            "LinkedIn PT":          ["python", "-u", "/app/scrapers/linkedin_scraper.py"],
            "Indeed PT":            ["python", "-u", "/app/scrapers/indeed_scraper.py"],
            "ITJobs":               ["python", "-u", "/app/scrapers/itjobs_scraper.py"],
            "Companies":            ["python", "-u", "/app/scrapers/companies_scraper.py"],
        }
        cmd = cmd_map.get(scrape_target, [])
        if cmd:
            post_cmd = ["python", "-u", "/app/automation/job_scorer.py"] if scrape_target != "Todas as Plataformas" else None
            user_label = selected_user_label if selected_user_id != "ALL" else "todos os utilizadores"
            run_in_terminal(cmd, env_vars=env_with_limit,
                            title=f"A procurar em {scrape_target} para {user_label}...",
                            post_cmd_list=post_cmd)

    st.divider()
    if st.button("🔄 Atualizar Dados", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("Manutenção")
    if st.button("Verificar Vagas Expiradas", use_container_width=True):
        env_vars = os.environ.copy()
        env_vars["PYTHONUNBUFFERED"] = "1"
        run_in_terminal(["python", "-u", "/app/automation/job_verifier.py"],
                        env_vars=env_vars, title="A verificar vagas expiradas...")


# ─────────────────────────────────────────────────────────────────────────────
# CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    header { visibility: hidden; }
    .stAppDeployButton { display: none !important; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    .block-container { padding-top: 1.5rem !important; padding-bottom: 0 !important; }
    .main { background-color: #FFFFFF; }
    div[data-testid="metric-container"] {
        background: #F9FAFB; border-radius: 12px; padding: 10px 15px;
        border: 1px solid #E5E7EB; box-shadow: 0 1px 2px rgba(0,0,0,.05);
    }
    div[data-testid="stMetricValue"] { font-size: 24px !important; font-weight: 700; color: #111827; }
    div[data-testid="stMetricLabel"] { font-size: 14px !important; color: #6B7280; }
    .stDataFrame { border: 1px solid #E5E7EB; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Router: detail view vs list view
# ─────────────────────────────────────────────────────────────────────────────
selected_id = st.query_params.get("id")


def show_detail_view(vaga_id):
    vaga_data = df[df['id'] == int(vaga_id)]
    if vaga_data.empty:
        st.error("Vaga não encontrada.")
        if st.button("Voltar para a lista"):
            st.query_params.clear()
            st.rerun()
        return

    v = vaga_data.iloc[0]
    c1, c2 = st.columns([0.15, 0.85])
    if c1.button("← Voltar"):
        st.query_params.clear()
        st.rerun()

    score_val = int(v['relevance_score'])
    score_badge = "🟢" if score_val >= 60 else ("🟡" if score_val >= 30 else "🔴")
    st.title(f"{v['titulo']}")
    st.subheader(f"{v['empresa']} — {v['localizacao']}  {score_badge} {score_val}/100")

    col1, col2, col3, col4 = st.columns(4)
    col1.markdown(f"**💰 Salário**\n\n{v['salario'] or 'Não especificado'}")
    col2.markdown(f"**📄 Contrato**\n\n{v['tipo_contrato'] or 'Não especificado'}")
    col3.markdown(f"**📈 Experiência**\n\n{v['nivel_experiencia'] or 'Não especificado'}")
    col4.markdown(f"**🌐 Fonte**\n\n{v['plataforma']}")

    st.divider()

    main_col, side_col = st.columns([0.7, 0.3])
    with main_col:
        st.markdown("### Descrição da Vaga")
        st.markdown(load_job_description(v['id']))
    with side_col:
        st.markdown("### Info Adicional")
        st.write(f"**Data Descoberta:** {_relative_date(v['data_scraped'])}")
        st.write(f"**Data Publicação:** {v['data_publicacao']}")
        if v['observacoes']:
            st.info(f"**Observações:**\n\n{v['observacoes']}")
        if v['recrutador_nome']:
            st.write(f"**Recrutador:** [{v['recrutador_nome']}]({v['recrutador_link']})")
        st.link_button("🚀 Candidatar-se Agora", v['link'], use_container_width=True, type="primary")


if selected_id:
    show_detail_view(selected_id)
else:
    if df.empty:
        st.warning("Base de dados vazia. Corre os scrapers primeiro.")
    else:
        # ── Running jobs alert ────────────────────────────────────────────────
        if is_job_running():
            st.warning("⚙️ **Jobs Running!** O sistema está a processar novas vagas em background.")

        # Placeholder rendered before filters; filled with compact header after
        # filtering so the metric counts reflect the active filter state.
        top_container = st.container()

        # ── Filter bar — Row 1 ───────────────────────────────────────────────
        fc1, fc2, fc3, fc4 = st.columns([3, 1, 1, 1])
        search    = fc1.text_input("Procurar (Título, Empresa, Local...)", placeholder="Ex: Python, Lisboa...")

        _raw_platforms = sorted(df['plataforma'].unique().tolist())
        _has_companies = any(p.startswith('Companies:') for p in _raw_platforms)
        _non_company   = [p for p in _raw_platforms if not p.startswith('Companies:')]
        plataformas_disponiveis = ["Todas"] + _non_company + (["Companies"] if _has_companies else [])

        if "main_platform" not in st.session_state:
            st.session_state.main_platform = "Todas"
        if st.session_state.main_platform not in plataformas_disponiveis:
            st.session_state.main_platform = "Todas"

        platform  = fc2.selectbox("Plataforma", plataformas_disponiveis, key="main_platform")
        status    = fc3.selectbox("Status", ["Todos", "Ativa", "Expirada", "Inacessível"], index=1)
        hoje_only = fc4.checkbox("Só hoje", value=False)

        # ── Filter bar — Row 2 ───────────────────────────────────────────────
        min_score_val = st.slider(
            "Score mínimo", min_value=0, max_value=100, value=0, step=5,
            help="0 = mostra tudo. Aumenta para filtrar vagas pouco relevantes.",
        )

        # ── Apply filters ─────────────────────────────────────────────────────
        df_f = df.copy()

        if search:
            mask = df_f[['titulo', 'empresa', 'localizacao', 'plataforma', 'observacoes']].apply(
                lambda col: col.astype(str).str.contains(search, case=False, regex=False, na=False)
            ).any(axis=1)
            df_f = df_f[mask]

        if platform != "Todas":
            if platform == "Companies":
                df_f = df_f[df_f['plataforma'].str.startswith('Companies:', na=False)]
            else:
                df_f = df_f[df_f['plataforma'] == platform]

        if status != "Todos":
            df_f = df_f[df_f['status'] == status]

        if hoje_only:
            today_prefix = datetime.now().strftime('%Y-%m-%d')
            df_f = df_f[df_f['data_scraped'].str.startswith(today_prefix, na=False)]

        if min_score_val > 0:
            df_f = df_f[df_f['relevance_score'] >= min_score_val]

        # ── Top section metrics ───────────────────────────────────────────────
        today_prefix = datetime.now().strftime('%Y-%m-%d')
        n_hoje = int(df_f['data_scraped'].str.startswith(today_prefix, na=False).sum())

        n_ativas  = int((df_f['status'] == 'Ativa').sum())
        n_empresas = int(df_f['empresa'].nunique())
        n_total    = len(df_f)
        with top_container:
            st.markdown(f"""
<div style="display:flex;align-items:center;gap:2.5rem;padding:0.4rem 0 0.6rem;
            border-bottom:1px solid #E5E7EB;margin-bottom:0.3rem;flex-wrap:wrap;">
  <div style="display:flex;align-items:center;gap:0.4rem;flex-shrink:0;">
    <span style="font-size:1.3rem;">🔍</span>
    <span style="font-size:1.05rem;font-weight:700;color:#111827;white-space:nowrap;">Pesquisa de Emprego</span>
  </div>
  <div style="display:flex;gap:2rem;align-items:center;flex-wrap:wrap;">
    <div style="text-align:center;">
      <div style="font-size:0.65rem;color:#9CA3AF;text-transform:uppercase;letter-spacing:.05em;line-height:1;">Total</div>
      <div style="font-size:1.25rem;font-weight:700;color:#111827;line-height:1.3;">{n_total}</div>
    </div>
    <div style="text-align:center;">
      <div style="font-size:0.65rem;color:#9CA3AF;text-transform:uppercase;letter-spacing:.05em;line-height:1;">Ativas</div>
      <div style="font-size:1.25rem;font-weight:700;color:#10b981;line-height:1.3;">{n_ativas}</div>
    </div>
    <div style="text-align:center;">
      <div style="font-size:0.65rem;color:#9CA3AF;text-transform:uppercase;letter-spacing:.05em;line-height:1;">Empresas</div>
      <div style="font-size:1.25rem;font-weight:700;color:#111827;line-height:1.3;">{n_empresas}</div>
    </div>
    <div style="text-align:center;">
      <div style="font-size:0.65rem;color:#9CA3AF;text-transform:uppercase;letter-spacing:.05em;line-height:1;">Hoje</div>
      <div style="font-size:1.25rem;font-weight:700;color:#6366f1;line-height:1.3;">{n_hoje}</div>
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

        # ── Build display dataframe ───────────────────────────────────────────
        df_disp = df_f.copy()

        # Score: coloured emoji label (replaces plain ProgressColumn)
        df_disp['Score'] = df_disp['relevance_score'].fillna(0).astype(int).apply(_score_label)

        # Simplify "Companies: Feedzai (Greenhouse)" → "Feedzai"
        def _fmt_fonte(p):
            if isinstance(p, str) and p.startswith('Companies:'):
                return p[len('Companies:'):].strip().split('(')[0].strip()
            return p
        df_disp['Fonte'] = df_disp['plataforma'].apply(_fmt_fonte)

        # Title: truncate to 45 chars
        df_disp['Vaga']    = df_disp['titulo'].apply(lambda x: (x[:42] + "…") if len(str(x)) > 45 else x)
        # Company: truncate to 25 chars
        df_disp['Empresa'] = df_disp['empresa'].apply(lambda x: (x[:22] + "…") if len(str(x)) > 25 else x)

        # Status icon (includes Inacessível)
        def _fmt_status(s):
            if s == 'Ativa':       return "🟢 Ativa"
            if s == 'Expirada':    return "🔴 Expirada"
            if s == 'Inacessível': return "🟠 Inacessível"
            return s
        df_disp['Estado'] = df_disp['status'].apply(_fmt_status)

        # Relative date
        df_disp['Criada em'] = df_disp['data_scraped'].apply(_relative_date)

        df_disp_view = df_disp.rename(columns={'id': 'ID', 'link': 'Abrir'})

        cols_to_show = ['Abrir', 'ID', 'Score', 'Vaga', 'Empresa', 'Fonte', 'Estado', 'Criada em']

        selection = st.dataframe(
            df_disp_view[cols_to_show],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "Abrir":      st.column_config.LinkColumn("🔗", display_text="Site", width="small"),
                "ID":         st.column_config.NumberColumn("ID", width="small"),
                "Score":      st.column_config.TextColumn("Score", width="small"),
                "Vaga":       st.column_config.TextColumn("Vaga", width="large"),
                "Empresa":    st.column_config.TextColumn("Empresa", width="medium"),
                "Fonte":      st.column_config.TextColumn("Fonte", width="medium"),
                "Estado":     st.column_config.TextColumn("Estado", width="small"),
                "Criada em":  st.column_config.TextColumn("Criada em", width="small"),
            }
        )

        if selection and selection.selection.rows:
            row_idx = selection.selection.rows[0]
            selected_vaga_id = df_disp.iloc[row_idx]['id']
            st.query_params.id = selected_vaga_id
            st.rerun()

        st.caption(f"**{len(df_f)}** vagas encontradas.")

st.markdown("---")
st.caption("Sistema de Monitorização de Emprego | © 2026")
