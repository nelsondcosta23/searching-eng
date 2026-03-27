import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime
import os

DB_PATH = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'database', 'vagas.db'))

st.set_page_config(
    page_title="Job Search Dashboard 🔍",
    page_icon="💼",
    layout="wide",
)

st.markdown("""
<style>
    .main { background-color: #F8F9FA; }
    .stApp header { background-color: #4F46E5; }

    div[data-testid="metric-container"] {
        background: white;
        border-radius: 12px;
        padding: 12px;
        border: 1px solid #E5E7EB;
        box-shadow: 0 1px 4px rgba(0,0,0,0.05);
    }
    .vaga-link a {
        color: #4F46E5 !important;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div style="background:#4F46E5;padding:28px 32px;border-radius:14px;margin-bottom:24px;">
    <h1 style="color:white;margin:0;">💼 Job Search Dashboard</h1>
    <p style="color:#C7D2FE;margin:6px 0 0;">All scraped jobs in one place, automatically updated.</p>
</div>
""", unsafe_allow_html=True)

@st.cache_data(ttl=60)
def load_jobs():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    # Schema v3
    df = pd.read_sql_query('''
        SELECT id, user_id, titulo, empresa, localizacao, plataforma, categoria, link, status,
               COALESCE(data_scraped, '') AS data_scraped,
               COALESCE(descricao_completa, 'No description loaded for this job.') AS descricao_completa
        FROM vagas
        ORDER BY data_scraped DESC
    ''', conn)
    conn.close()
    df['data_scraped'] = df['data_scraped'].astype(str)
    return df

df = load_jobs()

if df.empty:
    st.warning("Database not found or empty. Please run the scrapers first.")
else:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📋 Total Jobs", len(df))
    col2.metric("✅ Active Jobs", len(df[df['status'] == 'Ativa']))
    col3.metric("🏢 Unique Companies", df['empresa'].nunique())
    last_update = df['data_scraped'].dropna().max()
    col4.metric("📅 Last Update", last_update[:10] if last_update else "—")

    st.markdown("---")

    st.write("Filters")
    col_a, col_b, col_c, col_d, col_e = st.columns([2, 2, 2, 2, 2])

    search_txt = col_a.text_input("Keywords", placeholder="e.g. Python, Porto...")
    user_id_txt = col_b.text_input("User ID", placeholder="Filter by User...")
    status_options = ["All", "Ativa", "Expirada"]
    status_sel = col_c.selectbox("Status", status_options, index=1) # Default: Ativa
    platforms_available = ["All"] + sorted(df['plataforma'].unique().tolist())
    platform_sel = col_d.selectbox("Platform", platforms_available)
    categories_available = ["All"] + sorted(df['categoria'].dropna().unique().tolist())
    category_sel = col_e.selectbox("Category", categories_available)

    df_filtered = df.copy()

    if search_txt:
        mask = (
            df_filtered['titulo'].str.contains(search_txt, case=False, na=False) |
            df_filtered['empresa'].str.contains(search_txt, case=False, na=False) |
            df_filtered['localizacao'].str.contains(search_txt, case=False, na=False)
        )
        df_filtered = df_filtered[mask]
        
    if user_id_txt:
        df_filtered = df_filtered[df_filtered['user_id'].astype(str).str.contains(user_id_txt, case=False, na=False)]

    if status_sel != "All":
        df_filtered = df_filtered[df_filtered['status'] == status_sel]

    if platform_sel != "All":
        df_filtered = df_filtered[df_filtered['plataforma'] == platform_sel]

    if category_sel != "All":
        df_filtered = df_filtered[df_filtered['categoria'] == category_sel]

    st.markdown(f"**{len(df_filtered)} jobs found**")

    if df_filtered.empty:
        st.info("No jobs found with these filters.")
    else:
        # --- Paginação ---
        ITEMS_PER_PAGE = 30
        total_items = len(df_filtered)
        total_pages = max(1, (total_items - 1) // ITEMS_PER_PAGE + 1)
        
        col_space1, col_pag, col_space2 = st.columns([1, 2, 1])
        with col_pag:
            page = st.number_input("Navegar nas Páginas", min_value=1, max_value=total_pages, value=1, step=1)
            
        start_idx = (page - 1) * ITEMS_PER_PAGE
        end_idx = start_idx + ITEMS_PER_PAGE
        df_page = df_filtered.iloc[start_idx:end_idx]
        
        st.markdown(f"<p style='text-align:center; color:#6B7280; font-size:14px;'>A mostrar <b>{start_idx + 1}</b> a <b>{min(end_idx, total_items)}</b> de <b>{total_items}</b> vagas disponíveis nesta página.</p>", unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)
        
        # Prepare an interactive dataframe for display
        df_display = df_page.copy()
        
        # Format status to use emojis for native dataframe display
        df_display['status'] = df_display['status'].apply(lambda x: "🟢 Ativa" if x == 'Ativa' else "🔴 Expirada")
        
        # Map to friendly column names
        df_display = df_display.rename(columns={
            'status': 'Status',
            'titulo': 'Job Title',
            'empresa': 'Company',
            'localizacao': 'Location',
            'plataforma': 'Platform',
            'data_scraped': 'Date Found',
            'link': 'Application Link',
            'user_id': 'User ID',
            'descricao_completa': 'Full Description'
        })
        
        # Sub-select columns to show
        cols_to_show = [
            'Status', 'Job Title', 'Company', 'Location', 'Platform', 
            'Date Found', 'User ID', 'Full Description', 'Application Link'
        ]
        df_display = df_display[cols_to_show]

        # Render native Streamlit Dataframe with column configs
        st.dataframe(
            df_display,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Application Link": st.column_config.LinkColumn(
                    "Apply Here 🔗",
                    help="Click to open the job board",
                    max_chars=100,
                    display_text="Abrir Vaga"
                ),
                "Full Description": st.column_config.TextColumn(
                    "Job Description",
                    help="Hover or double click to expand and read the full text.",
                    width="large"
                )
            }
        )

st.markdown("---")
st.markdown(
    "<p style='text-align:center;color:#9CA3AF;font-size:13px;'>Automatic Job Scraping System | Data updated automatically via Cron.</p>",
    unsafe_allow_html=True
)
