import streamlit as st
import sqlite3
import contextlib
import pandas as pd
import os
import hmac
import psutil
import subprocess
from datetime import datetime, timedelta

st.set_page_config(
    page_title="JobRadar",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

DB_PATH            = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))
LOCK_FILE          = os.path.join(os.path.dirname(DB_PATH), 'orchestrator.lock')
DASHBOARD_PASSWORD = os.environ.get('DASHBOARD_PASSWORD', '').strip()

# PB-7: optional PocketBase data source.
# Set DASHBOARD_DATA_SOURCE=pocketbase to read job_results from PocketBase instead
# of direct SQLite. Useful for cloud/remote deployments where the DB is not mounted.
# Falls back to SQLite automatically if PocketBase is unreachable.
_USE_PB = os.environ.get('DASHBOARD_DATA_SOURCE', 'sqlite').lower() == 'pocketbase'


# ─────────────────────────────────────────────────────────────────────────────
# CSS — design system
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  /* ── Reset & base ── */
  .stAppDeployButton, #MainMenu, footer,
  [data-testid="stDecoration"] { display: none !important; }
  [data-testid="stHeader"] {
    background: transparent !important;
    border-bottom: none !important;
    box-shadow: none !important;
  }
  .block-container { padding: 1.5rem 2rem 2rem !important; max-width: 100% !important; }
  html, body, .main { background: #F3F4F6 !important; }

  /* ── Sidebar ── */
  [data-testid="stSidebar"] {
    background: #1E1B4B !important;
    border-right: none !important;
  }
  [data-testid="stSidebar"] * { color: #E0E7FF !important; }
  [data-testid="stSidebar"] .stSelectbox label,
  [data-testid="stSidebar"] .stTextInput label { color: #A5B4FC !important; font-size: 11px !important; text-transform: uppercase; letter-spacing: .06em; }
  [data-testid="stSidebar"] [data-testid="stSelectbox"] > div > div,
  [data-testid="stSidebar"] [data-testid="stTextInput"] input {
    background: #312E81 !important; border: 1px solid #4338CA !important;
    color: #E0E7FF !important; border-radius: 8px !important;
  }
  [data-testid="stSidebar"] [data-testid="stSelectbox"] svg { fill: #A5B4FC !important; }
  [data-testid="stSidebar"] hr { border-color: #3730A3 !important; opacity: .5; margin: .4rem 0 !important; }
  [data-testid="stSidebar"] [data-baseweb="select"] ul {
    background: #312E81 !important;
  }
  [data-testid="stSidebar"] [data-baseweb="select"] li:hover {
    background: #4338CA !important;
  }
  /* Compact sidebar vertical rhythm */
  [data-testid="stSidebar"] .stSelectbox { margin-bottom: 0 !important; padding-bottom: 0 !important; }
  [data-testid="stSidebar"] .stSelectbox > div { padding-bottom: 0 !important; }
  [data-testid="stSidebar"] .stButton { margin-top: .2rem !important; }
  [data-testid="stSidebar"] [data-testid="stVerticalBlockBorderWrapper"] { padding: 0 !important; }
  section[data-testid="stSidebar"] > div { padding-top: .75rem !important; padding-bottom: .5rem !important; }

  /* ── Primary button (sidebar) ── */
  [data-testid="stSidebar"] .stButton > button[kind="primary"] {
    background: linear-gradient(135deg, #6366F1 0%, #4F46E5 100%) !important;
    border: none !important; color: white !important;
    font-weight: 600 !important; border-radius: 10px !important;
    padding: 0.6rem 1rem !important; letter-spacing: .02em;
    box-shadow: 0 4px 14px rgba(99,102,241,.4) !important;
    transition: all .2s !important;
  }
  [data-testid="stSidebar"] .stButton > button[kind="primary"]:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 6px 20px rgba(99,102,241,.5) !important;
  }

  /* ── Secondary button (sidebar) ── */
  [data-testid="stSidebar"] .stButton > button:not([kind="primary"]) {
    background: #312E81 !important; border: 1px solid #4338CA !important;
    color: #C7D2FE !important; border-radius: 8px !important;
    font-weight: 500 !important; transition: all .15s !important;
  }
  [data-testid="stSidebar"] .stButton > button:not([kind="primary"]):hover {
    background: #3730A3 !important; border-color: #6366F1 !important;
  }

  /* ── Cards & surfaces ── */
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

  /* ── Filter bar ── */
  .filter-bar {
    background: white; border-radius: 12px; padding: .8rem 1rem;
    border: 1px solid #E5E7EB;
    box-shadow: 0 1px 3px rgba(0,0,0,.05);
    margin-bottom: .6rem;
  }
  .filter-bar .stTextInput input {
    border-radius: 8px !important; border: 1.5px solid #E5E7EB !important;
    font-size: 14px !important;
  }
  .filter-bar .stTextInput input:focus {
    border-color: #6366F1 !important; box-shadow: 0 0 0 3px rgba(99,102,241,.12) !important;
  }
  .filter-bar label { font-size: 11px !important; font-weight: 600 !important;
                      text-transform: uppercase; letter-spacing: .06em; color: #9CA3AF !important; }

  /* ── Table surface ── */
  .table-surface {
    background: white; border-radius: 14px;
    border: 1px solid #E5E7EB;
    box-shadow: 0 1px 3px rgba(0,0,0,.06);
    padding: 0 .5rem .5rem;
    overflow: hidden;
  }
  .stDataFrame { border: none !important; }
  .stDataFrame iframe { border-radius: 0 !important; }

  /* ── Detail view ── */
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

  /* ── Misc ── */
  .section-title {
    font-size: .7rem; font-weight: 700; text-transform: uppercase;
    letter-spacing: .1em; color: #9CA3AF; margin: .8rem 0 .4rem;
  }
  .running-banner {
    background: linear-gradient(135deg, #FEF3C7, #FDE68A);
    border: 1px solid #F59E0B; border-radius: 10px;
    padding: .6rem 1rem; margin-bottom: .8rem;
    font-size: .85rem; font-weight: 600; color: #92400E;
  }

  /* Score badges in table */
  .score-hi  { color: #065F46; font-weight: 700; }
  .score-mid { color: #92400E; font-weight: 700; }
  .score-lo  { color: #991B1B; font-weight: 700; }

  /* Back button */
  .stButton > button[data-testid="back-btn"] {
    background: #F3F4F6 !important; border: 1px solid #E5E7EB !important;
    color: #374151 !important; border-radius: 8px !important;
    font-weight: 500 !important;
  }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────
def _rel_date(date_str: str) -> str:
    if not date_str:
        return "—"
    try:
        dt   = datetime.strptime(date_str[:19], '%Y-%m-%d %H:%M:%S')
        diff = datetime.now() - dt
        if diff.days == 0:
            return f"Hoje {dt.strftime('%H:%M')}"
        if diff.days == 1:
            return f"Ontem {dt.strftime('%H:%M')}"
        if diff.days < 7:
            return f"Há {diff.days}d"
        return dt.strftime('%d %b')
    except Exception:
        return date_str[:10]


def _score_color(score: int) -> str:
    if score >= 60:
        return "score-hi"
    if score >= 30:
        return "score-mid"
    return "score-lo"


def _score_label(score: int) -> str:
    if score >= 60:
        return f"●  {score}"
    if score >= 30:
        return f"●  {score}"
    return f"●  {score}"


def _fmt_platform(p: str) -> str:
    if not isinstance(p, str):
        return p
    if p.startswith('Companies:'):
        return p[len('Companies:'):].strip().split('(')[0].strip()
    return p.split('(')[0].strip()  # "LinkedIn PT (Playwright)" → "LinkedIn PT"


def _pill(text: str, cls: str) -> str:
    return f"<span class='detail-pill {cls}'>{text}</span>"


def _status_pill(s: str) -> str:
    if s == 'Ativa':       return _pill("● Ativa", "pill-green")
    if s == 'Expirada':    return _pill("● Expirada", "pill-red")
    if s == 'Inacessível': return _pill("● Inacessível", "pill-amber")
    return _pill(s, "pill-gray")


def is_running() -> bool:
    if os.path.exists(LOCK_FILE):
        return True
    try:
        scripts = [
            "orchestrator.py", "job_verifier.py", "sapo_scraper.py",
            "expresso_scraper.py", "linkedin_scraper.py", "indeed_scraper.py",
            "itjobs_scraper.py", "companies_scraper.py",
        ]
        for proc in psutil.process_iter(['cmdline']):
            cmd = proc.info.get('cmdline')
            if cmd and any(s in " ".join(cmd) for s in scripts):
                return True
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────────────
@st.cache_data(ttl=60)
def _load_jobs_sqlite() -> pd.DataFrame:
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    with contextlib.closing(sqlite3.connect(DB_PATH, timeout=20, check_same_thread=False)) as conn:
        conn.execute('PRAGMA journal_mode=WAL;')
        return pd.read_sql_query('''
            SELECT jg.id, ju.user_id, jg.titulo, jg.empresa, jg.localizacao, jg.plataforma,
                   jg.categoria, jg.link, jg.status,
                   jg.salario, jg.tipo_contrato, jg.nivel_experiencia, jg.observacoes,
                   jg.recrutador_nome, jg.recrutador_link, jg.data_publicacao,
                   COALESCE(ju.relevance_score, 0) AS relevance_score,
                   COALESCE(jg.data_scraped, '') AS data_scraped
            FROM jobs_global jg
            JOIN jobs_users ju ON jg.id = ju.job_id
            WHERE jg.data_scraped >= date('now', '-45 days')
            ORDER BY COALESCE(ju.relevance_score, 0) DESC, jg.data_scraped DESC
        ''', conn)


@st.cache_data(ttl=60)
def _load_jobs_pb() -> pd.DataFrame:
    """Read job_results from PocketBase. Returns empty DataFrame on any error."""
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from automation.pb_client import get_client
        pb = get_client()
        if not pb.is_available():
            return pd.DataFrame()

        records = []
        page = 1
        while True:
            r = pb._req(
                'GET', '/api/collections/job_results/records',
                params={
                    'sort': '-relevance_score,-data_scraped',
                    'perPage': 500,
                    'page': page,
                },
            )
            if r is None or r.status_code != 200:
                break
            data = r.json()
            records.extend(data.get('items', []))
            if page >= data.get('totalPages', 1):
                break
            page += 1

        if not records:
            return pd.DataFrame()

        rows = []
        for i, rec in enumerate(records):
            rows.append({
                'id':                i,          # synthetic integer id for detail view routing
                '_pb_id':            rec.get('id', ''),
                'user_id':           rec.get('user_id', ''),
                'titulo':            rec.get('titulo', ''),
                'empresa':           rec.get('empresa', ''),
                'localizacao':       rec.get('localizacao', ''),
                'plataforma':        rec.get('plataforma', ''),
                'categoria':         rec.get('categoria', ''),
                'link':              rec.get('link', ''),
                'status':            rec.get('status', 'Ativa'),
                'salario':           rec.get('salario', ''),
                'tipo_contrato':     rec.get('tipo_contrato', ''),
                'nivel_experiencia': rec.get('nivel_experiencia', ''),
                'observacoes':       rec.get('observacoes', ''),
                'recrutador_nome':   rec.get('recrutador_nome', ''),
                'recrutador_link':   rec.get('recrutador_link', ''),
                'data_publicacao':   rec.get('data_publicacao', ''),
                'relevance_score':   int(rec.get('relevance_score') or 0),
                'data_scraped':      rec.get('data_scraped', ''),
                'descricao_completa': rec.get('descricao_completa', ''),
            })
        return pd.DataFrame(rows)
    except Exception as _e:
        print(f'[dashboard] PocketBase load failed: {_e}')
        return pd.DataFrame()


def load_jobs() -> pd.DataFrame:
    """Route to PocketBase or SQLite based on DASHBOARD_DATA_SOURCE env var.

    PocketBase mode falls back to SQLite automatically when PB is unreachable.
    """
    if _USE_PB:
        df_pb = _load_jobs_pb()
        if not df_pb.empty:
            return df_pb
        # Fallback: PB unavailable → read from local SQLite
    return _load_jobs_sqlite()


@st.cache_data(ttl=300)
def load_description(job_id: int) -> str:
    if not os.path.exists(DB_PATH):
        return ''
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=10)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            row = conn.execute("SELECT descricao FROM jobs_global WHERE id = ?", (job_id,)).fetchone()
        return (row[0] or '') if row else ''
    except Exception:
        return ''


@st.cache_data(ttl=120)
def load_users():
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


@st.cache_data(ttl=60)
def load_platform_stats():
    if not os.path.exists(DB_PATH):
        return {}
    try:
        with contextlib.closing(sqlite3.connect(DB_PATH, timeout=5)) as conn:
            conn.execute('PRAGMA journal_mode=WAL;')
            rows = conn.execute(
                "SELECT plataforma, status, COUNT(*) FROM jobs_global GROUP BY plataforma, status"
            ).fetchall()
        groups = ['Sapo Jobs', 'Expresso Jobs', 'LinkedIn PT', 'Indeed PT', 'ITJobs', 'Companies']
        out = {}
        for p in groups:
            key = p.split()[0].lower()
            out[p] = {
                'ativas':    sum(c for plat, st, c in rows if key in plat.lower() and st == 'Ativa'),
                'expiradas': sum(c for plat, st, c in rows if key in plat.lower() and st == 'Expirada'),
            }
        return out
    except Exception:
        return {}


# ─────────────────────────────────────────────────────────────────────────────
# Terminal dialog (run scrapers / verifier inline)
# ─────────────────────────────────────────────────────────────────────────────
@st.dialog("Terminal", width="large")
def terminal_dialog(cmd_list, env_vars=None, title="A executar…", post_cmd_list=None):
    st.markdown(f"<div style='font-weight:700;font-size:.95rem;margin-bottom:.75rem;'>{title}</div>",
                unsafe_allow_html=True)

    # Snapshot before the run (bypass cache for live accuracy)
    load_platform_stats.clear()
    init_stats = load_platform_stats()
    metrics_ph = st.empty()

    PLATFORM_LABELS = ['Sapo', 'Expresso', 'LinkedIn', 'Indeed', 'ITJobs', 'Companies']
    PLATFORM_KEYS   = ['Sapo Jobs', 'Expresso Jobs', 'LinkedIn PT', 'Indeed PT', 'ITJobs', 'Companies']

    def _render_metrics():
        load_platform_stats.clear()
        cur = load_platform_stats()
        cards = ""
        for label, key in zip(PLATFORM_LABELS, PLATFORM_KEYS):
            c_cur  = cur.get(key, {'ativas': 0, 'expiradas': 0})
            c_init = init_stats.get(key, {'ativas': 0, 'expiradas': 0})
            delta  = c_cur['ativas'] - c_init['ativas']
            delta_html = (f"<div style='color:#10b981;font-size:11px;font-weight:700;margin-top:2px;'>+{delta} novas</div>"
                          if delta > 0 else "")
            cards += f"""
<div style='flex:1;background:#F9FAFB;border:1px solid #E5E7EB;border-radius:10px;
            padding:10px 6px;text-align:center;min-width:0;'>
  <div style='font-size:10px;font-weight:700;text-transform:uppercase;
              letter-spacing:.05em;color:#6B7280;margin-bottom:4px;'>{label}</div>
  <div style='font-size:20px;font-weight:800;color:#111827;line-height:1;'>{c_cur['ativas']}</div>
  <div style='font-size:10px;color:#9CA3AF;margin-top:2px;'>ativas</div>
  {delta_html}
</div>"""
        # Single markdown call on the placeholder — always replaces, never appends
        metrics_ph.markdown(
            f"<div style='display:flex;gap:8px;margin-bottom:4px;'>{cards}</div>",
            unsafe_allow_html=True,
        )

    _render_metrics()
    st.markdown("<br>", unsafe_allow_html=True)

    log = ""
    with st.container(height=340):
        log_box = st.empty()

    proc = subprocess.Popen(cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, env=env_vars, bufsize=1)
    tick = 0
    for line in proc.stdout:
        log  += line
        log_box.code("\n".join(log.splitlines()[-150:]), language="bash")
        tick += 1
        if tick % 20 == 0:
            _render_metrics()
    proc.wait()
    _render_metrics()

    if post_cmd_list:
        st.markdown("<div style='font-weight:700;margin:.75rem 0 .4rem;'>A calcular relevância…</div>",
                    unsafe_allow_html=True)
        with st.container(height=180):
            post_box = st.empty()
        p2 = subprocess.Popen(post_cmd_list, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, env=env_vars, bufsize=1)
        post_log = ""
        for line in p2.stdout:
            post_log += line
            post_box.code("\n".join(post_log.splitlines()[-100:]), language="bash")
        p2.wait()

    st.success("Operação concluída!")
    if st.button("Fechar e atualizar", type="primary", use_container_width=True):
        st.cache_data.clear()
        st.query_params.clear()
        st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# D-3: Password gate (opt-in via DASHBOARD_PASSWORD env var)
# ─────────────────────────────────────────────────────────────────────────────
if DASHBOARD_PASSWORD:
    if not st.session_state.get('authenticated'):
        st.markdown("""
<div style='max-width:360px;margin:6rem auto;background:white;border-radius:16px;
            padding:2rem 2rem 1.5rem;border:1px solid #E5E7EB;
            box-shadow:0 4px 24px rgba(0,0,0,.08);'>
  <div style='font-size:1.5rem;font-weight:900;color:#1E1B4B;margin-bottom:.2rem;'>🎯 JobRadar</div>
  <div style='font-size:.8rem;color:#6B7280;margin-bottom:1.5rem;'>Acesso restrito</div>
</div>""", unsafe_allow_html=True)
        with st.form("login_form"):
            pwd = st.text_input("Palavra-passe", type="password", placeholder="••••••••")
            submitted = st.form_submit_button("Entrar", use_container_width=True, type="primary")
            if submitted:
                if DASHBOARD_PASSWORD and hmac.compare_digest(
                    pwd.encode('utf-8'), DASHBOARD_PASSWORD.encode('utf-8')
                ):
                    st.session_state['authenticated'] = True
                    st.rerun()
                else:
                    st.error("Palavra-passe incorrecta.")
        st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
df = load_jobs()

with st.sidebar:
    # Branding
    st.markdown("""
<div style='padding:.2rem 0 .6rem;'>
  <div style='font-size:1.2rem;font-weight:900;color:#E0E7FF;letter-spacing:-.02em;'>
    🎯 JobRadar
  </div>
  <div style='font-size:.65rem;color:#818CF8;font-weight:500;margin-top:.1rem;'>
    Sistema de monitorização de emprego
  </div>
</div>
""", unsafe_allow_html=True)

    # Quick links
    st.markdown("<div class='section-title' style='color:#818CF8;margin:.2rem 0 .3rem;'>Ferramentas</div>",
                unsafe_allow_html=True)
    st.markdown("""
<div style='display:flex;gap:5px;margin-bottom:2px;'>
  <a href='http://localhost:8091/_/' target='_blank' style='text-decoration:none;flex:1;'>
    <div style='background:#312E81;border:1px solid #4338CA;border-radius:8px;
                padding:5px 4px;text-align:center;transition:all .15s;'>
      <div style='font-size:13px;'>🗄️</div>
      <div style='font-size:9px;font-weight:700;color:#A5B4FC;margin-top:1px;'>PocketBase</div>
    </div>
  </a>
  <a href='http://localhost:3000' target='_blank' style='text-decoration:none;flex:1;'>
    <div style='background:#312E81;border:1px solid #4338CA;border-radius:8px;
                padding:5px 4px;text-align:center;'>
      <div style='font-size:13px;'>📊</div>
      <div style='font-size:9px;font-weight:700;color:#A5B4FC;margin-top:1px;'>Grafana</div>
    </div>
  </a>
  <a href='http://localhost:9090' target='_blank' style='text-decoration:none;flex:1;'>
    <div style='background:#312E81;border:1px solid #4338CA;border-radius:8px;
                padding:5px 4px;text-align:center;'>
      <div style='font-size:13px;'>📈</div>
      <div style='font-size:9px;font-weight:700;color:#A5B4FC;margin-top:1px;'>Prometheus</div>
    </div>
  </a>
</div>
""", unsafe_allow_html=True)

    st.divider()

    # Scraper controls
    st.markdown("<div class='section-title' style='color:#818CF8;margin:.1rem 0 .2rem;'>Pesquisa de Vagas</div>",
                unsafe_allow_html=True)

    all_users    = load_users()
    user_opts    = {"Todos os utilizadores": "ALL"}
    for uid, titles in all_users:
        label = (titles or "").split(',')[0].strip()[:28] or str(uid)[:8]
        user_opts[label] = uid

    sel_user_label = st.selectbox("Utilizador", list(user_opts.keys()), key="sb_user")
    sel_user_id    = user_opts[sel_user_label]

    def _sync_platform():
        t = st.session_state.sb_platform
        if t == "Todas as plataformas":
            st.session_state.main_platform = "Todas"
            return
        if t == "Companies":
            st.session_state.main_platform = "Companies"
            return
        base = t.split()[0]
        for p in (df['plataforma'].unique() if not df.empty else []):
            if base.lower() in p.lower():
                st.session_state.main_platform = p
                return
        st.session_state.main_platform = "Todas"

    sel_platform = st.selectbox("Plataforma", [
        "Todas as plataformas", "Sapo Jobs", "Expresso Jobs",
        "LinkedIn PT", "Indeed PT", "ITJobs", "Companies",
    ], key="sb_platform", on_change=_sync_platform)

    max_jobs = st.selectbox(
        "Limite de vagas",
        [0, 5, 10, 20, 50, 100],
        format_func=lambda x: "Sem limite" if x == 0 else str(x),
    )

    if st.button("▶ Pesquisar Novas Vagas", type="primary", use_container_width=True,
                 help="Corre o scraper para descarregar vagas novas da plataforma seleccionada"):
        env = os.environ.copy()
        env["MAX_JOBS_PER_PLATFORM"] = str(max_jobs)
        env["PYTHONUNBUFFERED"]      = "1"
        if sel_user_id != "ALL":
            env["TARGET_USER_ID"] = sel_user_id
        elif "TARGET_USER_ID" in env:
            del env["TARGET_USER_ID"]

        cmd_map = {
            "Todas as plataformas": ["python", "-u", "/app/automation/orchestrator.py"],
            "Sapo Jobs":            ["python", "-u", "/app/scrapers/sapo_scraper.py"],
            "Expresso Jobs":        ["python", "-u", "/app/scrapers/expresso_scraper.py"],
            "LinkedIn PT":          ["python", "-u", "/app/scrapers/linkedin_scraper.py"],
            "Indeed PT":            ["python", "-u", "/app/scrapers/indeed_scraper.py"],
            "ITJobs":               ["python", "-u", "/app/scrapers/itjobs_scraper.py"],
            "Companies":            ["python", "-u", "/app/scrapers/companies_scraper.py"],
        }
        cmd = cmd_map.get(sel_platform, [])
        if cmd:
            post = ["python", "-u", "/app/automation/job_scorer.py"] if sel_platform != "Todas as plataformas" else None
            who  = sel_user_label if sel_user_id != "ALL" else "todos os utilizadores"
            terminal_dialog(cmd, env_vars=env,
                            title=f"A pesquisar em {sel_platform} para {who}…",
                            post_cmd_list=post)

    st.divider()

    # Maintenance — hidden by default so it doesn't clutter the main workflow
    with st.expander("⚙️ Manutenção"):
        mc1, mc2 = st.columns(2)
        if mc1.button("✔ Verificar Expiradas", use_container_width=True,
                      help="Verifica links quebrados e marca vagas como Expiradas"):
            env = os.environ.copy()
            env["PYTHONUNBUFFERED"] = "1"
            terminal_dialog(["python", "-u", "/app/automation/job_verifier.py"],
                            env_vars=env, title="A verificar vagas expiradas…")

        if mc2.button("↺ Atualizar Dados", use_container_width=True,
                      help="Limpa a cache e recarrega os dados da BD"):
            st.cache_data.clear()
            st.rerun()

    # Footer
    st.markdown("""
<div style='text-align:center;font-size:.6rem;color:#4338CA;padding:.6rem 0 0;'>
  JobRadar © 2026
</div>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Detail view
# ─────────────────────────────────────────────────────────────────────────────
def show_detail(vaga_id: int):
    rows = df[df['id'] == vaga_id]
    if rows.empty:
        st.error("Vaga não encontrada.")
        if st.button("← Voltar"):
            st.query_params.clear(); st.rerun()
        return

    v = rows.iloc[0]
    score = int(v['relevance_score'])

    if st.button("← Voltar para a lista", key="back"):
        st.query_params.clear(); st.rerun()

    # Header card
    score_cls = "pill-green" if score >= 60 else ("pill-amber" if score >= 30 else "pill-red")
    plat_display = _fmt_platform(v['plataforma'])
    st.markdown(f"""
<div class='detail-header'>
  <div style='display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:.5rem;'>
    <div>
      <div style='font-size:1.4rem;font-weight:800;color:#111827;line-height:1.25;margin-bottom:.4rem;'>
        {v['titulo']}
      </div>
      <div style='font-size:1rem;color:#4B5563;font-weight:500;'>
        {v['empresa']} &nbsp;·&nbsp; {v['localizacao'] or 'Localização não especificada'}
      </div>
    </div>
    <div style='display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;'>
      {_status_pill(v['status'])}
      {_pill(f"Score {score}/100", score_cls)}
      {_pill(plat_display, 'pill-indigo')}
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

    # Info tiles row
    c1, c2, c3, c4 = st.columns(4)
    def _info_tile(col, label, value):
        col.markdown(f"""
<div class='info-block'>
  <div class='info-block-label'>{label}</div>
  <div class='info-block-value'>{value or '—'}</div>
</div>""", unsafe_allow_html=True)

    _info_tile(c1, "Salário",      v['salario'])
    _info_tile(c2, "Contrato",     v['tipo_contrato'])
    _info_tile(c3, "Experiência",  v['nivel_experiencia'])
    _info_tile(c4, "Descoberta",   _rel_date(v['data_scraped']))

    # Body
    left, right = st.columns([0.68, 0.32], gap="medium")

    with left:
        st.markdown("""<div style='font-size:.8rem;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#9CA3AF;margin:.5rem 0 .6rem;'>
                        Descrição da Vaga</div>""", unsafe_allow_html=True)
        # PB mode: description is already embedded in the row; SQLite mode: lazy fetch
        desc = v.get('descricao_completa') or load_description(v['id'])
        if desc:
            with st.container():
                st.markdown(desc)
        else:
            st.markdown("<div style='color:#9CA3AF;font-style:italic;'>Sem descrição disponível.</div>",
                        unsafe_allow_html=True)

    with right:
        st.markdown("""<div style='font-size:.8rem;font-weight:700;text-transform:uppercase;
                        letter-spacing:.08em;color:#9CA3AF;margin:.5rem 0 .6rem;'>
                        Detalhes</div>""", unsafe_allow_html=True)

        st.markdown(f"""
<div class='info-block'>
  <div class='info-block-label'>Data de publicação</div>
  <div class='info-block-value'>{v['data_publicacao'] or '—'}</div>
</div>""", unsafe_allow_html=True)

        if v['recrutador_nome']:
            st.markdown(f"""
<div class='info-block'>
  <div class='info-block-label'>Recrutador</div>
  <div class='info-block-value'>
    <a href='{v['recrutador_link']}' target='_blank'
       style='color:#4F46E5;text-decoration:none;font-weight:600;'>
      {v['recrutador_nome']}
    </a>
  </div>
</div>""", unsafe_allow_html=True)

        if v['observacoes']:
            st.markdown(f"""
<div style='background:#FFF7ED;border:1px solid #FED7AA;border-radius:10px;
            padding:.9rem 1rem;margin-bottom:.6rem;'>
  <div style='font-size:.65rem;font-weight:700;text-transform:uppercase;
              letter-spacing:.08em;color:#C2410C;margin-bottom:.3rem;'>Observações</div>
  <div style='font-size:.875rem;color:#92400E;'>{v['observacoes']}</div>
</div>""", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        st.link_button("Candidatar-se →", v['link'], use_container_width=True, type="primary")


# ─────────────────────────────────────────────────────────────────────────────
# List view
# ─────────────────────────────────────────────────────────────────────────────
def show_list():
    if df.empty:
        st.info("Base de dados vazia. Usa o painel lateral para iniciar uma pesquisa.")
        return

    today = datetime.now().strftime('%Y-%m-%d')

    # ── Session state for filters ─────────────────────────────────────────────
    if "main_platform" not in st.session_state:
        st.session_state.main_platform = "Todas"

    raw_plats   = sorted(df['plataforma'].unique().tolist())
    has_co      = any(p.startswith('Companies:') for p in raw_plats)
    non_co      = [p for p in raw_plats if not p.startswith('Companies:')]
    plat_opts   = ["Todas"] + non_co + (["Companies"] if has_co else [])
    if st.session_state.main_platform not in plat_opts:
        st.session_state.main_platform = "Todas"

    # ── User filter options (from DB, restricted to users that have jobs) ──────
    active_user_ids = set(df['user_id'].unique()) if not df.empty else set()
    filter_user_opts: dict = {"Todos": "ALL"}
    for uid, titles in load_users():
        if uid in active_user_ids:
            label = (titles or "").split(',')[0].strip()[:22] or str(uid)[:10]
            filter_user_opts[label] = uid
    # Fallback: users in df but not in users_perfil (shouldn't happen normally)
    for uid in sorted(active_user_ids):
        if uid not in filter_user_opts.values():
            filter_user_opts[str(uid)[:10] + "…"] = uid

    # ── Filter bar ────────────────────────────────────────────────────────────
    with st.container():
        fc1, fc2, fc3, fc4, fc5, fc6 = st.columns([2.5, 1.4, 1.5, 1, 1.2, 0.8])
        search          = fc1.text_input("Pesquisar", placeholder="🔍  Título, empresa, localização…")
        sel_filter_user = fc2.selectbox("Utilizador", list(filter_user_opts.keys()), key="filter_user")
        platform        = fc3.selectbox("Plataforma", plat_opts, key="main_platform")
        status_filter   = fc4.selectbox("Estado", ["Todas", "Ativa", "Expirada", "Inacessível"], index=1)
        min_score       = fc5.slider("Score mín.", 0, 100, 0, 5)
        hoje_only       = fc6.checkbox("Só hoje", value=False)

    filter_user_id = filter_user_opts[sel_filter_user]

    # ── Apply filters ─────────────────────────────────────────────────────────
    dff = df.copy()

    if filter_user_id != "ALL":
        dff = dff[dff['user_id'] == filter_user_id]

    if search:
        mask = dff[['titulo', 'empresa', 'localizacao', 'plataforma', 'observacoes']].apply(
            lambda col: col.astype(str).str.contains(search, case=False, regex=False, na=False)
        ).any(axis=1)
        dff = dff[mask]

    if platform != "Todas":
        if platform == "Companies":
            dff = dff[dff['plataforma'].str.startswith('Companies:', na=False)]
        else:
            dff = dff[dff['plataforma'] == platform]

    if status_filter != "Todas":
        dff = dff[dff['status'] == status_filter]

    if hoje_only:
        dff = dff[dff['data_scraped'].str.startswith(today, na=False)]

    if min_score > 0:
        dff = dff[dff['relevance_score'] >= min_score]

    # ── KPI row ───────────────────────────────────────────────────────────────
    n_total   = len(dff)
    n_ativas  = int((dff['status'] == 'Ativa').sum())
    n_hoje    = int(dff['data_scraped'].str.startswith(today, na=False).sum())
    n_emp     = int(dff['empresa'].nunique())
    top_score = int(dff['relevance_score'].max()) if n_total else 0

    k1, k2, k3, k4, k5 = st.columns(5)
    _kpi_hoje = datetime.now().strftime("%-d %b %Y")
    with k1:
        st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Vagas encontradas</div><div class='kpi-value' style='color:#4F46E5;'>{n_total}</div><div class='kpi-sub'>nas últimas 45 dias</div></div>", unsafe_allow_html=True)
    with k2:
        st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Ativas</div><div class='kpi-value' style='color:#10B981;'>{n_ativas}</div><div class='kpi-sub'>de {n_total} no filtro</div></div>", unsafe_allow_html=True)
    with k3:
        st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Hoje</div><div class='kpi-value' style='color:#6366F1;'>{n_hoje}</div><div class='kpi-sub'>{_kpi_hoje}</div></div>", unsafe_allow_html=True)
    with k4:
        st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Empresas</div><div class='kpi-value' style='color:#F59E0B;'>{n_emp}</div><div class='kpi-sub'>distintas</div></div>", unsafe_allow_html=True)
    with k5:
        st.markdown(f"<div class='kpi-card'><div class='kpi-label'>Melhor score</div><div class='kpi-value' style='color:#EF4444;'>{top_score}</div><div class='kpi-sub'>/100 no filtro atual</div></div>", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── Table ─────────────────────────────────────────────────────────────────
    dff2 = dff.copy()
    dff2['Score']      = dff2['relevance_score'].fillna(0).astype(int).apply(_score_label)
    dff2['Vaga']       = dff2['titulo'].apply(lambda x: (str(x)[:50] + "…") if len(str(x)) > 52 else x)
    dff2['Empresa']    = dff2['empresa'].apply(lambda x: (str(x)[:26] + "…") if len(str(x)) > 28 else x)
    dff2['Fonte']      = dff2['plataforma'].apply(_fmt_platform)
    dff2['Estado']     = dff2['status'].map({'Ativa': '● Ativa', 'Expirada': '● Expirada',
                                              'Inacessível': '● Inacessível'}).fillna(dff2['status'])
    dff2['Quando']     = dff2['data_scraped'].apply(_rel_date)
    dff2['Local']      = dff2['localizacao'].apply(lambda x: (str(x)[:22] + "…") if len(str(x)) > 24 else x)
    dff2 = dff2.rename(columns={'id': 'ID', 'link': 'Link'})

    # Row selection: single-row for detail navigation
    with st.container():
        st.markdown("<div class='table-surface'>", unsafe_allow_html=True)

        selection = st.dataframe(
            dff2[['Link', 'Score', 'Vaga', 'Empresa', 'Local', 'Fonte', 'Estado', 'Quando']],
            use_container_width=True,
            hide_index=True,
            on_select="rerun",
            selection_mode="single-row",
            height=520,
            column_config={
                "Link":   st.column_config.LinkColumn("", display_text="↗", width=36),
                "Score":  st.column_config.TextColumn("Score", width=72),
                "Vaga":   st.column_config.TextColumn("Título", width="large"),
                "Empresa":st.column_config.TextColumn("Empresa", width="medium"),
                "Local":  st.column_config.TextColumn("Local", width="small"),
                "Fonte":  st.column_config.TextColumn("Fonte", width="small"),
                "Estado": st.column_config.TextColumn("Estado", width="small"),
                "Quando": st.column_config.TextColumn("Quando", width="small"),
            },
        )
        st.markdown("</div>", unsafe_allow_html=True)

    # Detail navigation
    if selection and selection.selection.rows:
        idx = selection.selection.rows[0]
        # Use dff2 (which still has 'ID') for routing — subset above doesn't include ID
        vid = dff2.iloc[idx]['ID']
        st.query_params.id = int(vid)
        st.rerun()

    # Footer row: count + CSV export
    bot_left, bot_right = st.columns([3, 1])
    bot_left.markdown(
        f"<div style='font-size:.75rem;color:#9CA3AF;padding-top:.4rem;'>"
        f"<b>{n_total}</b> vagas encontradas · Clica numa linha para ver os detalhes</div>",
        unsafe_allow_html=True,
    )
    csv_cols = ['titulo', 'empresa', 'localizacao', 'plataforma', 'link',
                'relevance_score', 'status', 'salario', 'tipo_contrato',
                'nivel_experiencia', 'data_scraped', 'data_publicacao']
    csv_data = dff[[c for c in csv_cols if c in dff.columns]].to_csv(index=False).encode('utf-8')
    bot_right.download_button(
        "📥 Exportar CSV",
        data=csv_data,
        file_name=f"vagas_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Router
# ─────────────────────────────────────────────────────────────────────────────
selected_id = st.query_params.get("id")
if selected_id:
    show_detail(int(selected_id))
else:
    show_list()
