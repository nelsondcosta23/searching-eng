import streamlit as st
import sqlite3
import contextlib
import pandas as pd
import os
import psutil

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
LOCK_FILE = os.path.join(os.path.dirname(DB_PATH), 'orchestrator.lock')

def is_job_running():
    """Checks if any scraper or orchestrator is running locally or via lock file."""
    # 1. Check shared lock file (Global, works for Cron too)
    if os.path.exists(LOCK_FILE):
        return True
    
    # 2. Check local processes (Manual runs from UI)
    try:
        scripts = [
            "orchestrator.py", "job_verifier.py",
            "sapo_scraper.py", "expresso_scraper.py", "linkedin_scraper.py",
            "indeed_scraper.py", "itjobs_scraper.py",
            "companies_scraper.py",
        ]
        for proc in psutil.process_iter(['cmdline']):
            cmdline = proc.info.get('cmdline')
            if cmdline:
                if any(s in " ".join(cmdline) for s in scripts):
                    return True
    except:
        pass
    return False

st.set_page_config(
    page_title="Job Search Dashboard",
    page_icon="🔍",
    layout="wide",
)

import subprocess

@st.cache_data(ttl=60)
def load_jobs():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    with contextlib.closing(sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)) as conn:
        conn.execute('PRAGMA journal_mode=WAL;')
        # descricao_completa excluded here — fetched on demand in detail view to keep memory low
        return pd.read_sql_query('''
            SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link, status,
                   salario, tipo_contrato, nivel_experiencia, observacoes,
                   recrutador_nome, recrutador_link, data_publicacao,
                   COALESCE(relevance_score, 0) AS relevance_score,
                   COALESCE(data_scraped, '') AS data_scraped
            FROM vagas
            WHERE data_scraped >= date('now', '-45 days')
            ORDER BY data_scraped DESC
        ''', conn)


@st.cache_data(ttl=300)
def load_job_description(job_id: int) -> str:
    """Fetches descricao_completa for a single job — called only when detail view opens."""
    if not os.path.exists(DB_PATH):
        return 'Sem descrição disponível.'
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            row = conn.execute(
                "SELECT descricao_completa FROM vagas WHERE id = ?", (job_id,)
            ).fetchone()
        return (row[0] if row and row[0] else 'Sem descrição disponível.')
    except Exception:
        return 'Sem descrição disponível.'

df = load_jobs()

def get_users_perfil():
    """Loads all active user profiles from the local DB for the sidebar selector."""
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
            ativas = sum(c for plat, st, c in rows if key in plat.lower() and st == 'Ativa')
            expiradas = sum(c for plat, st, c in rows if key in plat.lower() and st == 'Expirada')
            metrics[p] = {'ativas': ativas, 'expiradas': expiradas}
        return metrics
    except Exception:
        return {}

@st.dialog("Terminal de Execução", width="large")
def run_in_terminal(cmd_list, env_vars=None, title="A executar...", post_cmd_list=None):
    st.write(f"**{title}**")
    
    initial_stats = get_platform_metrics()
    metrics_placeholder = st.empty()
    
    def update_metrics():
        current_stats = get_platform_metrics()
        if current_stats and initial_stats:
            with metrics_placeholder.container():
                cols = st.columns(7)
                platforms = ['Sapo Jobs', 'Expresso Jobs', 'LinkedIn PT', 'Indeed PT', 'ITJobs', 'Companies']
                for i, p in enumerate(platforms):
                    short_name = p.replace(" PT", "").replace(" Jobs", "")
                    curr = current_stats.get(p, {'ativas': 0, 'expiradas': 0})
                    init = initial_stats.get(p, {'ativas': 0, 'expiradas': 0})
                    
                    diff_active = curr['ativas'] - init['ativas']
                    diff_exp = curr['expiradas'] - init['expiradas']
                    
                    # Construção das linhas de delta
                    active_delta = f"<div style='color:#10b981; font-size:11px; margin-top:-5px;'>+{diff_active} novas</div>" if diff_active > 0 else ""
                    exp_delta = f"<div style='color:#ef4444; font-size:11px; margin-top:-5px;'>+{diff_exp} novas</div>" if diff_exp > 0 else ""
                    
                    with cols[i]:
                        html_content = f"""<div style='background: #fdfdfd; padding: 12px 5px; border-radius: 10px; border: 1px solid #eee; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.02);'>
<div style='color: #666; font-size: 12px; font-weight: 600; text-transform: uppercase; margin-bottom: 5px;'>{short_name}</div>
<div style='color: #10b981; font-size: 16px; font-weight: 700;'>{curr['ativas']} <span style='font-size:10px; font-weight:400;'>Ativas</span></div>
{active_delta}
<div style='color: #ef4444; font-size: 14px; font-weight: 600; margin-top: 5px;'>{curr['expiradas']} <span style='font-size:10px; font-weight:400;'>Exp.</span></div>
{exp_delta}
</div>"""
                        st.markdown(html_content, unsafe_allow_html=True)

    update_metrics()
    st.markdown("<br>", unsafe_allow_html=True)
    
    log_content = ""
    with st.container(height=350):
        log_box = st.empty()
    
    process = subprocess.Popen(
        cmd_list, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.STDOUT, 
        text=True, 
        env=env_vars,
        bufsize=1
    )
    
    loop_counter = 0
    for line in process.stdout:
        log_content += line
        # Keep up to 150 lines to allow scrolling without lag
        lines = log_content.splitlines()[-150:]
        log_box.code("\n".join(lines), language="bash")
        
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
            post_cmd_list, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT, 
            text=True, 
            env=env_vars,
            bufsize=1
        )
        post_log_content = ""
        for line in post_process.stdout:
            post_log_content += line
            lines = post_log_content.splitlines()[-100:]
            post_log_box.code("\n".join(lines), language="bash")
        post_process.wait()

    st.success("Operação concluída com sucesso!")
    if st.button("Fechar e Atualizar o Dashboard", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.query_params.clear()
        st.rerun()

with st.sidebar:
    st.header("⚡ Ações Rápidas")
    st.caption("Podes forçar atualizações diretamente por aqui.")
    
    def sync_platform_filter():
        target = st.session_state.sidebar_platform
        if target == "Todas as Plataformas":
            st.session_state.main_platform = "Todas"
            return
        # Companies uses a synthetic filter value (multiple platforms start with "Companies:")
        if target == "Companies":
            st.session_state.main_platform = "Companies"
            return

        base_name = target.split(' ')[0]  # 'Indeed', 'LinkedIn', 'Sapo', etc.
        for p in df['plataforma'].unique().tolist():
            if base_name.lower() in p.lower():
                st.session_state.main_platform = p
                return

        st.session_state.main_platform = "Todas"

    st.subheader("Procurar Novas Vagas")

    # --- User Selector ---
    all_users = get_users_perfil()
    user_options = {"🌐 Todos os Utilizadores": "ALL"}
    for uid, titles in all_users:
        display = (titles or "").split(',')[0].strip()[:30] or str(uid)[:8]
        label = f"👤 {display}"
        user_options[label] = uid

    selected_user_label = st.selectbox(
        "Utilizador",
        list(user_options.keys()),
        key="sidebar_user"
    )
    selected_user_id = user_options[selected_user_label]

    scrape_target = st.selectbox("Plataforma", [
        "Todas as Plataformas",
        "Sapo Jobs",
        "Expresso Jobs",
        "LinkedIn PT",
        "Indeed PT",
        "ITJobs",
        "Companies",
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

        # Pass selected user (or ALL) to the scraper via env var
        if selected_user_id != "ALL":
            env_with_limit["TARGET_USER_ID"] = selected_user_id
        elif "TARGET_USER_ID" in env_with_limit:
            del env_with_limit["TARGET_USER_ID"]
        
        cmd = []
        if scrape_target == "Todas as Plataformas":
            cmd = ["python", "-u", "/app/automation/orchestrator.py"]
        elif scrape_target == "Sapo Jobs":
            cmd = ["python", "-u", "/app/scrapers/sapo_scraper.py"]
        elif scrape_target == "Expresso Jobs":
            cmd = ["python", "-u", "/app/scrapers/expresso_scraper.py"]
        elif scrape_target == "LinkedIn PT":
            cmd = ["python", "-u", "/app/scrapers/linkedin_scraper.py"]
        elif scrape_target == "Indeed PT":
            cmd = ["python", "-u", "/app/scrapers/indeed_scraper.py"]
        elif scrape_target == "ITJobs":
            cmd = ["python", "-u", "/app/scrapers/itjobs_scraper.py"]
        elif scrape_target == "Companies":
            cmd = ["python", "-u", "/app/scrapers/companies_scraper.py"]

        if cmd:
            user_label = selected_user_label if selected_user_id != "ALL" else "todos os utilizadores"
            post_cmd = ["python", "-u", "/app/automation/job_scorer.py"] if scrape_target != "Todas as Plataformas" else None
            run_in_terminal(cmd, env_vars=env_with_limit, title=f"A procurar em {scrape_target} para {user_label}...", post_cmd_list=post_cmd)

    st.divider()
    
    if st.button("🔄 Atualizar Dados", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("Manutenção")
    if st.button("Verificar Vagas Expiradas", use_container_width=True):
        env_vars = os.environ.copy()
        env_vars["PYTHONUNBUFFERED"] = "1"
        run_in_terminal(["python", "-u", "/app/automation/job_verifier.py"], env_vars=env_vars, title="A verificar vagas expiradas...")
        
    st.sidebar.divider()
    # Deploy Status removed as requested

# Custom Minimalist CSS
st.markdown("""
<style>
    /* Hide default Streamlit elements */
    header { visibility: hidden; }
    .stAppDeployButton { display: none !important; }
    #MainMenu { visibility: hidden; }
    footer { visibility: hidden; }
    
    /* Reduzir margem do topo */
    .block-container {
        padding-top: 1.5rem !important;
        padding-bottom: 0rem !important;
    }
    .main { background-color: #FFFFFF; }
    
    /* Estilo dos Widgets de Métricas */
    div[data-testid="metric-container"] {
        background: #F9FAFB;
        border-radius: 12px;
        padding: 10px 15px;
        border: 1px solid #E5E7EB;
        box-shadow: 0 1px 2px rgba(0,0,0,0.05);
    }
    div[data-testid="stMetricValue"] {
        font-size: 24px !important;
        font-weight: 700;
        color: #111827;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 14px !important;
        color: #6B7280;
    }
    .stDataFrame { border: 1px solid #E5E7EB; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# --- Navigation Logic ---
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

    # Header with Back Button
    c1, c2 = st.columns([0.15, 0.85])
    if c1.button("← Voltar"):
        st.query_params.clear()
        st.rerun()
    
    st.title(f"{v['titulo']}")
    st.subheader(f"{v['empresa']} — {v['localizacao']}")
    
    # Metadata Row
    col1, col2, col3, col4 = st.columns(4)
    col1.markdown(f"**💰 Salário**\n\n{v['salario'] or 'Não especificado'}")
    col2.markdown(f"**📄 Contrato**\n\n{v['tipo_contrato'] or 'Não especificado'}")
    col3.markdown(f"**📈 Experiência**\n\n{v['nivel_experiencia'] or 'Não especificado'}")
    col4.markdown(f"**🌐 Fonte**\n\n{v['plataforma']}")

    st.divider()

    # Description and Sidebar
    main_col, side_col = st.columns([0.7, 0.3])
    
    with main_col:
        st.markdown("### Descrição da Vaga")
        st.markdown(load_job_description(v['id']))
        
    with side_col:
        st.markdown("### Info Adicional")
        st.write(f"**Data Descoberta:** {v['data_scraped']}")
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
        # --- 1. Top Section Placeholders (Title & Metrics) ---
        top_container = st.container()
        
        # --- Alerta de Jobs em Background ---
        if is_job_running():
            st.warning("⚙️ **Jobs Running!** O sistema está a processar novas vagas em background.")
        
        # --- 2. Filters Row (Placed above the table) ---
        st.write("") # Spacer
        c1, c_u, c2, c3 = st.columns([2, 1, 1, 1])
        
        search = c1.text_input("Procurar (Título, Empresa, Local...)", placeholder="Ex: Python, Lisboa...")
        
        # User Filter
        all_users_db = get_users_perfil()
        user_map = {"Todos": "Todos"}
        for uid, titles in all_users_db:
            user_map[uid] = f"ID: {str(uid)[:5]}"
                
        user_filter = c_u.selectbox("Utilizador", list(user_map.keys()), format_func=lambda x: user_map[x])

        if "main_platform" not in st.session_state:
            st.session_state.main_platform = "Todas"
            
        _raw_platforms = sorted(df['plataforma'].unique().tolist())
        # Aggregate all "Companies: ..." entries into a single "Companies" option
        _has_companies = any(p.startswith('Companies:') for p in _raw_platforms)
        _non_company = [p for p in _raw_platforms if not p.startswith('Companies:')]
        plataformas_disponiveis = ["Todas"] + _non_company + (["Companies"] if _has_companies else [])
        if st.session_state.main_platform not in plataformas_disponiveis:
            st.session_state.main_platform = "Todas"
            
        platform = c2.selectbox("Plataforma", plataformas_disponiveis, key="main_platform")
        status = c3.selectbox("Status", ["Todos", "Ativa", "Expirada"], index=1)

        # --- 3. Execute Filtering ---
        df_f = df.copy()
        if search:
            search_cols = ['titulo', 'empresa', 'localizacao', 'plataforma', 'observacoes']
            mask = df_f[search_cols].apply(
                lambda col: col.astype(str).str.contains(search, case=False, regex=False, na=False)
            ).any(axis=1)
            df_f = df_f[mask]
        if user_filter != "Todos":
            df_f = df_f[df_f['user_id'] == user_filter]
        if platform != "Todas":
            if platform == "Companies":
                df_f = df_f[df_f['plataforma'].str.startswith('Companies:', na=False)]
            else:
                df_f = df_f[df_f['plataforma'] == platform]
        if status != "Todos":
            df_f = df_f[df_f['status'] == status]

        # --- 4. Fill Top Section (Title & Metrics) ---
        with top_container:
            t1, m1, m2, m3 = st.columns([2, 1, 1, 1])
            with t1:
                st.title("🔍 Pesquisa de Emprego")
                st.caption("Agregador de vagas em tempo real. Seleciona uma vaga para ver detalhes.")
            
            m1.metric("Total de Vagas", len(df_f))
            m2.metric("Vagas Ativas", len(df_f[df_f['status'] == 'Ativa']))
            m3.metric("Empresas", df_f['empresa'].nunique())
            st.divider()


        # Prepare Display Dataframe
        df_disp = df_f.copy()
        
        # Map user_id to friendly name
        df_disp['Perfil'] = df_disp['user_id'].apply(lambda x: f"ID: {str(x)[:5]}")

        # Simplify "Companies: Feedzai (Greenhouse)" → "Feedzai"
        def _fmt_fonte(p):
            if isinstance(p, str) and p.startswith('Companies:'):
                inner = p[len('Companies:'):].strip()
                return inner.split('(')[0].strip()
            return p
        df_disp['plataforma'] = df_disp['plataforma'].apply(_fmt_fonte)
        
        # Truncate Title to 40 characters
        df_disp['Vaga'] = df_disp['titulo'].apply(lambda x: (x[:37] + "...") if len(x) > 40 else x)
        
        # Status icon
        df_disp['Estado'] = df_disp['status'].apply(lambda x: "🟢 Ativa" if x == 'Ativa' else "🔴 Expirada")

        df_disp_view = df_disp.rename(columns={
            'id': 'ID',
            'plataforma': 'Fonte',
            'data_scraped': 'Criada em',
            'link': 'Abrir'
        })

        df_disp_view['Score'] = df_disp['relevance_score'].fillna(0).astype(int)

        cols_to_show = ['Abrir', 'ID', 'Score', 'Perfil', 'Vaga', 'Fonte', 'Estado', 'Criada em']

        # New selection feature in st.dataframe (Streamlit 1.35+)
        selection = st.dataframe(
            df_disp_view[cols_to_show],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            column_config={
                "Abrir": st.column_config.LinkColumn("🔗", display_text="Site"),
                "ID": st.column_config.NumberColumn("ID", width="small"),
                "Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=100, format="%d", width="small"),
                "Perfil": st.column_config.TextColumn("Perfil", width="medium"),
                "Vaga": st.column_config.TextColumn("Vaga", width="medium"),
                "Estado": st.column_config.TextColumn("Estado", width="small"),
                "Criada em": st.column_config.TextColumn("Criada em", width="medium")
            }
        )

        # Check for selection and trigger detail view
        if selection and selection.selection.rows:
            row_idx = selection.selection.rows[0]
            selected_vaga_id = df_disp.iloc[row_idx]['id']
            st.query_params.id = selected_vaga_id
            st.rerun()
            
        st.caption(f"**{len(df_f)}** vagas encontradas.")

st.markdown("---")
st.caption("Sistema de Monitorização de Emprego | © 2026")

