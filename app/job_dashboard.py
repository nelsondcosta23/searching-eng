import streamlit as st
import sqlite3
import contextlib
import pandas as pd
import os
import sys

# Add project root to path so we can import 'automation' modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
import psutil
import subprocess
from datetime import datetime, timedelta

st.set_page_config(
    page_title="Tech Job Market — Portugal",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
LOCK_FILE = os.path.join(os.path.dirname(DB_PATH), 'orchestrator.lock')

# ─────────────────────────────────────────────────────────────────────────────
# CSS Design System
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* Base reset and styling */
  .stAppDeployButton, #MainMenu, footer, [data-testid="stDecoration"] { display: none !important; }
  [data-testid="stHeader"] {
    background: transparent !important;
    border-bottom: none !important;
    box-shadow: none !important;
  }
  .block-container { padding: 1.5rem 2rem 2rem !important; max-width: 100% !important; }
  html, body, .main { background: #F3F4F6 !important; }

  /* Sidebar styling */
  [data-testid="stSidebar"] {
    background: #111827 !important;
    border-right: none !important;
  }
  [data-testid="stSidebar"] * { color: #F3F4F6 !important; }
  [data-testid="stSidebar"] .stSelectbox label,
  [data-testid="stSidebar"] .stTextInput label { color: #9CA3AF !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: .06em; }
  [data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div,
  [data-testid="stSidebar"] [data-testid="stTextInput"] input {
    background: #1F2937 !important; border: 1px solid #374151 !important;
    color: #F3F4F6 !important; border-radius: 8px !important;
  }
  [data-testid="stSidebar"] [data-testid="stSelectbox"] svg { fill: #9CA3AF !important; }
  [data-testid="stSidebar"] hr { border-color: #374151 !important; opacity: .5; margin: .4rem 0 !important; }

  /* Buttons */
  [data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #4F46E5 0%, #312E81 100%) !important;
    border: none !important; color: white !important;
    font-weight: 600 !important; border-radius: 10px !important;
    padding: 0.6rem 1rem !important;
    box-shadow: 0 4px 14px rgba(79,70,229,.4) !important;
    transition: all .2s !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 20px rgba(79,70,229,.5) !important;
  }
  [data-testid="stSidebar"] .stButton > button:not([kind="primary"]) {
    background: #1F2937 !important; border: 1px solid #374151 !important;
    color: #D1D5DB !important; border-radius: 8px !important;
    font-weight: 500 !important;
  }
  [data-testid="stSidebar"] .stButton > button:not([kind="primary"]):hover {
    background: #374151 !important; border-color: #4F46E5 !important;
  }

  /* KPI Cards */
  .kpi-card {
    background: white; border-radius: 14px;
    padding: 1.2rem 1.4rem; border: 1px solid #E5E7EB;
    box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    height: 100%;
  }
  .kpi-value { font-size: 2rem; font-weight: 800; color: #111827; line-height: 1.1; }
  .kpi-label { font-size: .7rem; font-weight: 600; text-transform: uppercase;
               letter-spacing: .08em; color: #9CA3AF; margin-bottom: .35rem; }
  .kpi-sub   { font-size: .75rem; color: #6B7280; margin-top: .25rem; }

  /* Surfaces & Headers */
  .detail-header {
    background: white; border-radius: 14px; padding: 1.5rem 1.8rem;
    border: 1px solid #E5E7EB; box-shadow: 0 1px 3px rgba(0,0,0,.06);
    margin-bottom: 1rem;
  }
  .detail-pill {
    display: inline-block; padding: .25rem .75rem; border-radius: 99px;
    font-size: .72rem; font-weight: 600; letter-spacing: .03em;
  }
  .pill-green { background:#D1FAE5; color:#065F46; }
  .pill-red   { background:#FEE2E2; color:#991B1B; }
  .pill-amber { background:#FEF3C7; color:#92400E; }
  .pill-blue  { background:#DBEAFE; color:#1E40AF; }
  .pill-indigo{ background:#E0E7FF; color:#3730A3; }
  .pill-gray  { background:#F3F4F6; color:#374151; }

  .info-block {
    background: #FAFAFA; border: 1px solid #E5E7EB;
    border-radius: 10px; padding: .9rem 1rem; margin-bottom: .6rem;
  }
  .info-block-label { font-size: .65rem; font-weight: 700; text-transform: uppercase;
                      letter-spacing: .08em; color: #9CA3AF; margin-bottom: .3rem; }
  .info-block-value { font-size: .95rem; font-weight: 600; color: #111827; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Data Fetching Helpers
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=30)
def load_all_jobs() -> pd.DataFrame:
    """Loads all tech jobs (active and expired)."""
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            df = pd.read_sql_query('''
                SELECT id, plataforma, titulo, empresa, localizacao, link,
                       salario, tipo_contrato, nivel_experiencia, job_type,
                       status, recrutador_nome, recrutador_link, data_publicacao, data_scraped, posting_age_days
                FROM jobs
                WHERE job_type != 'Non-tech'
                ORDER BY data_scraped DESC
            ''', conn)
            return df
    except Exception as e:
        st.error(f"Error loading jobs: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def load_company_analytics() -> pd.DataFrame:
    """Loads aggregated company metrics."""
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    try:
        from automation.job_analytics import get_company_rankings
        return pd.DataFrame(get_company_rankings())
    except Exception as e:
        st.error(f"Error loading company rankings: {e}")
        return pd.DataFrame()

@st.cache_data(ttl=60)
def load_job_type_dist() -> pd.DataFrame:
    """Loads job type distribution."""
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    try:
        from automation.job_analytics import get_job_type_distribution
        return pd.DataFrame(get_job_type_distribution())
    except Exception as e:
        st.error(f"Error loading job type distribution: {e}")
        return pd.DataFrame()

def load_job_description(job_id: int) -> str:
    """Fetches job description text from database."""
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=5)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            row = conn.execute("SELECT descricao FROM jobs WHERE id = ?", (job_id,)).fetchone()
            return (row[0] or '') if row else ''
    except Exception:
        return ''

def get_last_run_timestamp() -> str:
    """Gets the timestamp of the last job scraped."""
    if not os.path.exists(DB_PATH):
        return "N/A"
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH)) as conn:
            row = conn.execute("SELECT MAX(data_scraped) FROM jobs").fetchone()
            if row and row[0]:
                dt = datetime.strptime(row[0][:19], '%Y-%m-%d %H:%M:%S')
                diff = datetime.now() - dt
                if diff.days == 0:
                    return f"Today at {dt.strftime('%H:%M')}"
                return f"{diff.days} days ago"
    except Exception:
        pass
    return "N/A"

def is_scraper_running() -> bool:
    """Checks if there is a running orchestrator lock or subprocess."""
    if os.path.exists(LOCK_FILE):
        return True
    try:
        for proc in psutil.process_iter(['cmdline']):
            cmd = proc.info.get('cmdline')
            if cmd and any('orchestrator.py' in s for s in cmd):
                return True
    except Exception:
        pass
    return False

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar Controls & Scraper trigger
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("""
    <div style='padding:.2rem 0 .6rem;'>
      <div style='font-size:1.4rem;font-weight:900;color:#F3F4F6;'>🎯 Tech Job Market</div>
      <div style='font-size:.65rem;color:#9CA3AF;font-weight:500;margin-top:.1rem;'>Portugal</div>
    </div>
    """, unsafe_allow_html=True)
    
    st.divider()
    
    st.markdown("<div style='font-size:11px;font-weight:600;text-transform:uppercase;color:#9CA3AF;'>Trigger Scraper</div>", unsafe_allow_html=True)
    
    selected_platform = st.selectbox(
        "Platform Target",
        ["All Scrapers", "LinkedIn PT", "Sapo Jobs", "Indeed PT", "ITJobs", "Companies", "Landing.jobs", "Expresso Jobs"]
    )
    
    run_btn = st.button("▶ Run Intelligence Scrape", type="primary", use_container_width=True)
    
    if run_btn:
        if is_scraper_running():
            st.warning("Scraper is already running! Please wait.")
        else:
            # Map friendly option name to script parameter
            param_map = {
                "All Scrapers": "",
                "LinkedIn PT": "linkedin",
                "Sapo Jobs": "sapo",
                "Indeed PT": "indeed",
                "ITJobs": "itjobs",
                "Companies": "companies",
                "Landing.jobs": "landing",
                "Expresso Jobs": "expresso"
            }
            param = param_map[selected_platform]
            cmd = [sys.executable, "-u", "automation/orchestrator.py"]
            if param:
                cmd.extend(["--scrapers", param])
                
            st.info(f"Running scraper subprocess...")
            with st.status("Scraping in progress...", expanded=True) as status:
                log_ph = st.empty()
                log_lines = []
                
                # Execute orchestrator as a subprocess and stream output
                proc = subprocess.Popen(
                    cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1
                )
                
                for line in proc.stdout:
                    log_lines.append(line)
                    # Keep output display readable (show last 50 lines)
                    log_ph.code("".join(log_lines[-50:]), language="bash")
                    
                proc.wait()
                
                if proc.returncode == 0:
                    status.update(label="Scraping & Analysis Completed!", state="complete")
                    st.success("Successfully finished run!")
                    # Clear caches so display updates immediately
                    st.cache_data.clear()
                    st.rerun()
                else:
                    status.update(label="Run Failed", state="error")
                    st.error(f"Scraper returned error code {proc.returncode}")

    st.divider()
    
    # Utilities
    if st.button("↺ Refresh Dashboard", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
        
    st.divider()
    st.markdown("<div style='text-align:center;font-size:.6rem;color:#4B5563;'>Tech Job Market Portugal v3.0</div>", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Load Data
# ─────────────────────────────────────────────────────────────────────────────
df_jobs = load_all_jobs()
df_rankings = load_company_analytics()
df_job_types = load_job_type_dist()

# ─────────────────────────────────────────────────────────────────────────────
# Header & KPI Row
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("<div style='font-size:1.8rem;font-weight:900;color:#111827;margin-bottom:1rem;'>Tech Job Market — Portugal</div>", unsafe_allow_html=True)

if is_scraper_running():
    st.markdown("""
    <div style='background:#FEF3C7;border:1px solid #F59E0B;border-radius:10px;padding:.6rem 1rem;margin-bottom:1rem;color:#92400E;font-weight:600;font-size:.85rem;'>
      ⚠️ Scraper pipeline is currently active in the background. Refresh data in a few minutes.
    </div>
    """, unsafe_allow_html=True)

c1, c2, c3, c4 = st.columns(4)

with c1:
    total_active = len(df_jobs) if not df_jobs.empty else 0
    st.markdown(f"""
    <div class='kpi-card'>
      <div class='kpi-label'>Active Tech Positions</div>
      <div class='kpi-value'>{total_active}</div>
      <div class='kpi-sub'>Filtered tech roles</div>
    </div>
    """, unsafe_allow_html=True)

with c2:
    total_companies = df_jobs['empresa'].nunique() if not df_jobs.empty else 0
    st.markdown(f"""
    <div class='kpi-card'>
      <div class='kpi-label'>Companies Hiring</div>
      <div class='kpi-value'>{total_companies}</div>
      <div class='kpi-sub'>Unique employers in IT</div>
    </div>
    """, unsafe_allow_html=True)

with c3:
    top_type = df_job_types.iloc[0]['job_type'] if not df_job_types.empty else "N/A"
    top_count = df_job_types.iloc[0]['count'] if not df_job_types.empty else 0
    st.markdown(f"""
    <div class='kpi-card'>
      <div class='kpi-label'>Top Job Category</div>
      <div class='kpi-value' style='font-size:1.4rem;padding-top:0.4rem;padding-bottom:0.2rem;'>{top_type}</div>
      <div class='kpi-sub'>{top_count} active vacancies</div>
    </div>
    """, unsafe_allow_html=True)

with c4:
    freshness = get_last_run_timestamp()
    st.markdown(f"""
    <div class='kpi-card'>
      <div class='kpi-label'>Last Run Freshness</div>
      <div class='kpi-value' style='font-size:1.4rem;padding-top:0.4rem;padding-bottom:0.2rem;'>{freshness}</div>
      <div class='kpi-sub'>Time since last scraped vacancy</div>
    </div>
    """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Market Report Tabs
# ─────────────────────────────────────────────────────────────────────────────
t1, t2 = st.tabs(["🏢 Vista por Empresa", "🔍 Exploração de Vagas"])

# Tab 1: Analytics & Reports
with t1:
    col_l, col_r = st.columns([0.65, 0.35], gap="large")
    
    with col_l:
        st.markdown("### Ranking de Empresas Recrutadoras")
        if df_rankings.empty:
            st.info("Sem dados de ranking disponíveis. Execute o scraper primeiro.")
        else:
            # We want to present: Company, Age, Open Tech positions, Job type list.
            # Combine rankings with detail breakdowns.
            try:
                from automation.job_analytics import get_company_job_types
                df_types = pd.DataFrame(get_company_job_types())
            except Exception:
                df_types = pd.DataFrame()
            
            # Pivot types to format nicely
            breakdown_dict = {}
            if not df_types.empty:
                for _, row in df_types.iterrows():
                    co = row['empresa']
                    jt = row['job_type']
                    cnt = row['count']
                    breakdown_dict.setdefault(co, []).append(f"{jt} ({cnt})")
            
            # Build pretty view dataframe
            report_rows = []
            for _, row in df_rankings.iterrows():
                co = row['empresa']
                age = f"{int(row['company_age'])} anos" if pd.notnull(row['company_age']) else "Desconhecido"
                founded = f"{int(row['inception_year'])}" if pd.notnull(row['inception_year']) else "—"
                
                # Fetch posting age stats
                ages = df_jobs[df_jobs['empresa'].str.lower() == co.lower()]['posting_age_days'].dropna()
                avg_age = f"{round(ages.mean(), 1)} dias" if not ages.empty else "N/D"
                
                breakdown_str = ", ".join(breakdown_dict.get(co, []))
                
                report_rows.append({
                    "Empresa": co,
                    "Ano de Fundação": founded,
                    "Idade da Empresa": age,
                    "Nº de Vagas Tech": int(row['open_positions']),
                    "Repartição por Categoria": breakdown_str,
                    "Idade Média das Vagas": avg_age
                })
            
            df_report = pd.DataFrame(report_rows)
            st.dataframe(df_report, use_container_width=True, hide_index=True)
            
            # Download Markdown Report Option
            try:
                from automation.job_analytics import generate_markdown_report
                md_content = generate_markdown_report()
                st.download_button(
                    label="📥 Descarregar Relatório de Inteligência (Markdown)",
                    data=md_content,
                    file_name=f"relatorio_mercado_tech_{datetime.now().strftime('%Y-%m-%d')}.md",
                    mime="text/markdown"
                )
            except Exception:
                pass

    with col_r:
        st.markdown("### Distribuição por Categoria")
        if df_job_types.empty:
            st.info("Nenhuma categoria para exibir.")
        else:
            # Display distribution as a chart
            st.bar_chart(
                df_job_types.set_index("job_type")["count"],
                color="#4F46E5"
            )
            
            # Table listing
            st.dataframe(
                df_job_types.rename(columns={"job_type": "Categoria", "count": "Vagas"}),
                use_container_width=True,
                hide_index=True
            )

# Tab 2: Job Explorer & Details
with t2:
    if df_jobs.empty:
        st.info("Nenhuma vaga armazenada na base de dados. Use a barra lateral para executar uma recolha.")
    else:
        # Check if user has selected a job ID
        selected_job_id = st.query_params.get("job_id")
        
        if selected_job_id:
            # ─────────────────────────────────────────────────────────────────
            # DETAIL VIEW
            # ─────────────────────────────────────────────────────────────────
            job_id_int = int(selected_job_id)
            rows = df_jobs[df_jobs['id'] == job_id_int]
            if rows.empty:
                st.error("Os detalhes da vaga selecionada não foram encontrados.")
                if st.button("← Voltar para a lista"):
                    st.query_params.clear()
                    st.rerun()
            else:
                v = rows.iloc[0]
                
                # Back Button
                if st.button("← Voltar para a lista", key="back_btn_top"):
                    st.query_params.clear()
                    st.rerun()
                
                # Header Title Card
                st.markdown(f"""
                <div class='detail-header'>
                  <div style='display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:.5rem;'>
                    <div>
                      <div style='font-size:1.5rem;font-weight:800;color:#111827;line-height:1.2;margin-bottom:.4rem;'>
                        {v['titulo']}
                      </div>
                      <div style='font-size:1rem;color:#4B5563;font-weight:500;'>
                        🏢 {v['empresa']} &nbsp;·&nbsp; 📍 {v['localizacao'] or 'Localização não especificada'}
                      </div>
                    </div>
                    <div style='display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;'>
                      <span class='detail-pill pill-indigo'>{v['job_type']}</span>
                      <span class='detail-pill pill-green'>{v['plataforma'].split('(')[0].strip()}</span>
                    </div>
                  </div>
                </div>
                """, unsafe_allow_html=True)
                
                # Info Tiles
                ti_c1, ti_c2, ti_c3, ti_c4 = st.columns(4)
                
                age_val = f"{int(v['posting_age_days'])} dias" if pd.notnull(v['posting_age_days']) else "Recente"
                
                ti_c1.markdown(f"""<div class='info-block'><div class='info-block-label'>Idade Estimada</div><div class='info-block-value'>{age_val}</div></div>""", unsafe_allow_html=True)
                ti_c2.markdown(f"""<div class='info-block'><div class='info-block-label'>Intervalo Salarial</div><div class='info-block-value'>{v['salario'] or 'Não divulgado'}</div></div>""", unsafe_allow_html=True)
                ti_c3.markdown(f"""<div class='info-block'><div class='info-block-label'>Tipo de Contrato</div><div class='info-block-value'>{v['tipo_contrato'] or 'Não especificado'}</div></div>""", unsafe_allow_html=True)
                ti_c4.markdown(f"""<div class='info-block'><div class='info-block-label'>Experiência</div><div class='info-block-value'>{v['nivel_experiencia'] or 'Não especificada'}</div></div>""", unsafe_allow_html=True)
                
                # Job description and sidebar layout
                layout_l, layout_r = st.columns([0.7, 0.3], gap="large")
                
                with layout_l:
                    st.markdown("<div style='font-size:.8rem;font-weight:700;text-transform:uppercase;color:#9CA3AF;'>Descrição do Cargo</div>", unsafe_allow_html=True)
                    desc_text = load_job_description(v['id'])
                    if desc_text:
                        st.text_area("", value=desc_text, height=450, disabled=True)
                    else:
                        st.markdown("*Nenhum texto de descrição disponível.*")
                
                with layout_r:
                    st.markdown("<div style='font-size:.8rem;font-weight:700;text-transform:uppercase;color:#9CA3AF;'>Metadados e Ações</div>", unsafe_allow_html=True)
                    
                    st.markdown(f"""
                    <div class='info-block'>
                      <div class='info-block-label'>Plataforma de Extração</div>
                      <div class='info-block-value'>{v['plataforma']}</div>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if v['recrutador_nome']:
                        st.markdown(f"""
                        <div class='info-block'>
                          <div class='info-block-label'>Perfil do Recrutador</div>
                          <div class='info-block-value'>
                            <a href='{v['recrutador_link']}' target='_blank' style='color:#4F46E5;text-decoration:none;'>
                              👤 {v['recrutador_nome']}
                            </a>
                          </div>
                        </div>
                        """, unsafe_allow_html=True)
                        
                    st.link_button("Candidatar-se à Vaga 🚀", v['link'], type="primary", use_container_width=True)
                    
        else:
            # ─────────────────────────────────────────────────────────────────
            # LIST VIEW WITH FILTERS
            # ─────────────────────────────────────────────────────────────────
            
            # Interactive Filter Bar
            with st.container():
                st.markdown("<div style='font-size:12px;font-weight:600;text-transform:uppercase;color:#9CA3AF;margin-bottom:4px;'>Filtrar Vagas</div>", unsafe_allow_html=True)
                f_c1, f_c2, f_c3, f_c4 = st.columns([2, 1, 1, 1])
                
                search_q = f_c1.text_input("Termo de pesquisa", placeholder="🔍 Pesquisar por Título, Empresa ou Localização...", label_visibility="collapsed")
                
                # Job Type list
                avail_types = sorted(df_jobs['job_type'].dropna().unique().tolist())
                sel_type = f_c2.selectbox("Tipo de Vaga", ["Todos os Tipos"] + avail_types, index=0, label_visibility="collapsed")
                
                # Platform List
                avail_plats = sorted(df_jobs['plataforma'].apply(lambda x: x.split('(')[0].strip()).unique().tolist())
                sel_plat = f_c3.selectbox("Plataforma", ["Todas as Plataformas"] + avail_plats, index=0, label_visibility="collapsed")
                
                # Estado (Status) filter - default to Ativa
                sel_status = f_c4.selectbox("Estado", ["Ativa", "Expirada", "Todas"], index=0, label_visibility="collapsed")
                
            # Filter Dataframe
            df_filtered = df_jobs.copy()
            
            if search_q:
                q = search_q.lower()
                df_filtered = df_filtered[
                    df_filtered['titulo'].str.lower().str.contains(q) | 
                    df_filtered['empresa'].str.lower().str.contains(q) | 
                    df_filtered['localizacao'].str.lower().str.contains(q)
                ]
                
            if sel_type != "Todos os Tipos":
                df_filtered = df_filtered[df_filtered['job_type'] == sel_type]
                
            if sel_plat != "Todas as Plataformas":
                df_filtered = df_filtered[df_filtered['plataforma'].str.startswith(sel_plat)]
                
            if sel_status != "Todas":
                df_filtered = df_filtered[df_filtered['status'] == sel_status]
                
            c_count, c_export = st.columns([3, 1])
            with c_count:
                st.markdown(f"<div style='font-size:.75rem;color:#6B7280;margin-top:.4rem;margin-bottom:.6rem;'>A mostrar {len(df_filtered)} vagas correspondentes</div>", unsafe_allow_html=True)
            with c_export:
                if not df_filtered.empty:
                    csv_data = df_filtered.to_csv(index=False).encode('utf-8')
                    st.download_button(
                        label="📥 Exportar para CSV",
                        data=csv_data,
                        file_name=f"vagas_tech_{datetime.now().strftime('%Y-%m-%d')}.csv",
                        mime="text/csv",
                        use_container_width=True
                    )
            
            # Format display dataframe
            if df_filtered.empty:
                st.info("Nenhuma vaga corresponde aos filtros selecionados.")
            else:
                display_cols = ["titulo", "empresa", "job_type", "link", "plataforma", "status", "data_publicacao", "posting_age_days"]
                df_show = df_filtered[display_cols].copy()
                
                # Clean platform display
                df_show['plataforma'] = df_show['plataforma'].apply(lambda x: x.split('(')[0].strip())
                df_show['posting_age_days'] = df_show['posting_age_days'].apply(lambda x: f"{int(x)} dias" if pd.notnull(x) else "Recente")
                
                df_show.rename(columns={
                    "titulo": "Título",
                    "empresa": "Empresa",
                    "job_type": "Tipo",
                    "link": "Link",
                    "plataforma": "Plataforma",
                    "status": "Estado",
                    "data_publicacao": "Extraído em",
                    "posting_age_days": "Idade Estimada"
                }, inplace=True)
                
                # Render list selection with link column config
                selected_row = st.dataframe(
                    df_show,
                    column_config={
                        "Link": st.column_config.LinkColumn("URL", display_text="Abrir ↗")
                    },
                    use_container_width=True,
                    hide_index=True,
                    on_select="rerun",
                    selection_mode="single-row"
                )
                
                # If a row is selected, route to job details using query params
                if selected_row and selected_row.get("selection", {}).get("rows"):
                    idx = selected_row["selection"]["rows"][0]
                    target_job_id = df_filtered.iloc[idx]["id"]
                    st.query_params["job_id"] = str(target_job_id)
                    st.rerun()
