import sqlite3
import os

# Define the path for the database file (inside the persistent folder)
# When running inside Docker, this points to /app/database/vagas.db
db_path = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'vagas.db'))

print(f"Connecting to database at: {db_path}...")

conn = None
try:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()

    # Create the 'vagas' table (Schema v6 — adds salario, tipo_contrato, nivel_experiencia, relevance_score)
    print("Creating/updating 'vagas' table (Schema v6)...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vagas (
            id                        INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id                   TEXT,
            plataforma                TEXT NOT NULL,
            id_externo                TEXT,
            titulo                    TEXT NOT NULL,
            empresa                   TEXT,
            localizacao               TEXT,
            data_publicacao           TEXT,
            data_scraped              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_ultima_verificacao   TIMESTAMP DEFAULT NULL,
            link                      TEXT NOT NULL UNIQUE,
            descricao_completa        TEXT,
            status                    TEXT DEFAULT 'Ativa',
            categoria                 TEXT,
            status_envio              INTEGER DEFAULT 0,
            recrutador_nome           TEXT,
            recrutador_link           TEXT,
            observacoes               TEXT,
            salario                   TEXT,
            tipo_contrato             TEXT,
            nivel_experiencia         TEXT,
            relevance_score           INTEGER,
            CONSTRAINT unique_vaga_platform UNIQUE (plataforma, id_externo)
        )
    ''')

    # Migrate existing databases: add new columns if they don't exist yet
    existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(vagas)").fetchall()]
    migrations = [
        ("salario",                  "TEXT"),
        ("tipo_contrato",            "TEXT"),
        ("nivel_experiencia",        "TEXT"),
        ("relevance_score",          "INTEGER"),
        ("data_ultima_verificacao",  "TIMESTAMP"),
    ]
    for col_name, col_type in migrations:
        if col_name not in existing_cols:
            cursor.execute(f"ALTER TABLE vagas ADD COLUMN {col_name} {col_type}")
            print(f"  ✅ Migrated: added column '{col_name}'")
        else:
            print(f"  ✓  Column '{col_name}' already exists.")

    conn.commit()
    print("Database and table successfully initialized (Schema v6)!")

    # Create the 'users_perfil' table for personalized user preferences
    print("Creating/updating 'users_perfil' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users_perfil (
            user_id             TEXT PRIMARY KEY,
            is_active           INTEGER DEFAULT 1,
            job_titles          TEXT NOT NULL,
            keywords            TEXT,
            negative_keywords   TEXT,
            negative_companies  TEXT,
            locations           TEXT,
            is_remote           INTEGER DEFAULT 0,
            min_salary          INTEGER DEFAULT 0,
            experience_levels   TEXT,
            job_profile         TEXT DEFAULT 'generalist',
            contract_type       TEXT,
            required_languages  TEXT,
            search_description  TEXT,
            callback_url        TEXT,
            last_webhook_sent   TIMESTAMP,
            created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # Migrate existing users_perfil: add new columns if they don't exist
    existing_user_cols = [row[1] for row in cursor.execute("PRAGMA table_info(users_perfil)").fetchall()]
    user_migrations = [
        ("keywords",            "TEXT"),
        ("negative_keywords",   "TEXT"),
        ("negative_companies",  "TEXT"),
        ("callback_url",        "TEXT"),
        ("last_webhook_sent",   "TIMESTAMP"),
        ("is_active",           "INTEGER DEFAULT 1"),
        ("job_profile",         "TEXT DEFAULT 'generalist'"),
        ("contract_type",       "TEXT"),
        ("required_languages",  "TEXT"),
        ("search_description",  "TEXT"),
    ]
    for col_name, col_type in user_migrations:
        if col_name not in existing_user_cols:
            cursor.execute(f"ALTER TABLE users_perfil ADD COLUMN {col_name} {col_type}")
            print(f"  ✅ Migrated users_perfil: added column '{col_name}'")
        else:
            print(f"  ✓  Column '{col_name}' already exists in users_perfil.")

    # Create high-performance indexes for scrapers
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_active ON users_perfil(is_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_active_remote ON users_perfil(is_active, is_remote)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_users_active_created ON users_perfil(is_active, created_at)')

    # Hot-path indexes on `vagas` (Phase B — perf optimization, audit 2026-05-09)
    print("Creating performance indexes on 'vagas'...")
    vagas_indexes = [
        # Covers: API list/stats by user, webhook today's jobs, ORDER BY data_scraped DESC
        ('idx_vagas_user_scraped',    'CREATE INDEX IF NOT EXISTS idx_vagas_user_scraped ON vagas(user_id, data_scraped DESC)'),
        # Covers: API status filter, stats grouping by status, verifier "Ativa" lookups scoped per user
        ('idx_vagas_user_status',     'CREATE INDEX IF NOT EXISTS idx_vagas_user_status ON vagas(user_id, status)'),
        # Partial index — only covers unscored rows; small footprint, dramatic scorer speedup
        ('idx_vagas_relevance_null',  'CREATE INDEX IF NOT EXISTS idx_vagas_relevance_null ON vagas(user_id) WHERE relevance_score IS NULL'),
        # Covers: verifier with the "skip recently-verified" filter (Phase C)
        ('idx_vagas_status_verif',    'CREATE INDEX IF NOT EXISTS idx_vagas_status_verif ON vagas(status, data_ultima_verificacao)'),
        # Covers: weekly cleanup `WHERE data_scraped < ?`
        ('idx_vagas_data_scraped',    'CREATE INDEX IF NOT EXISTS idx_vagas_data_scraped ON vagas(data_scraped)'),
        # Covers: API platform LIKE filter (helps when narrowed by user_id first via composite above)
        ('idx_vagas_plataforma',      'CREATE INDEX IF NOT EXISTS idx_vagas_plataforma ON vagas(plataforma)'),
    ]
    for name, sql in vagas_indexes:
        cursor.execute(sql)
        print(f"  ✓  {name}")

    conn.commit()
    print("User profile schema successfully initialized!")

    # ─────────────────────────────────────────────────────────────────────
    # Schema v7 — Global job deduplication
    # ─────────────────────────────────────────────────────────────────────
    print("Creating/updating 'jobs_global' table (Schema v7)...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs_global (
            id                      INTEGER PRIMARY KEY,
            link                    TEXT NOT NULL UNIQUE,
            titulo                  TEXT,
            empresa                 TEXT,
            plataforma              TEXT,
            id_externo              TEXT,
            localizacao             TEXT,
            categoria               TEXT,
            descricao               TEXT,
            observacoes             TEXT,
            recrutador_nome         TEXT,
            recrutador_link         TEXT,
            salario                 TEXT,
            tipo_contrato           TEXT,
            nivel_experiencia       TEXT,
            data_publicacao         TEXT,
            data_scraped            TEXT,
            data_ultima_verificacao TEXT,
            status                  TEXT DEFAULT 'Ativa'
        )
    ''')

    print("Creating/updating 'jobs_users' table (Schema v7)...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs_users (
            job_id          INTEGER NOT NULL REFERENCES jobs_global(id),
            user_id         TEXT    NOT NULL,
            relevance_score INTEGER,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (job_id, user_id)
        )
    ''')

    # Indexes for hot query paths (scorer, API, webhook)
    v7_indexes = [
        ('idx_jg_link',         'CREATE INDEX IF NOT EXISTS idx_jg_link         ON jobs_global(link)'),
        ('idx_jg_status',       'CREATE INDEX IF NOT EXISTS idx_jg_status       ON jobs_global(status)'),
        ('idx_jg_data_scraped', 'CREATE INDEX IF NOT EXISTS idx_jg_data_scraped ON jobs_global(data_scraped)'),
        ('idx_ju_user',         'CREATE INDEX IF NOT EXISTS idx_ju_user         ON jobs_users(user_id)'),
        ('idx_ju_user_score',   'CREATE INDEX IF NOT EXISTS idx_ju_user_score   ON jobs_users(user_id, relevance_score)'),
        ('idx_ju_unscored',     'CREATE INDEX IF NOT EXISTS idx_ju_unscored     ON jobs_users(user_id) WHERE relevance_score IS NULL'),
    ]
    for name, sql in v7_indexes:
        cursor.execute(sql)
        print(f"  ✓  {name}")

    conn.commit()
    print("Schema v7 (jobs_global + jobs_users) initialized!")

    # ─────────────────────────────────────────────────────────────────────
    # Schema v8 — Audit log (D-4)
    # ─────────────────────────────────────────────────────────────────────
    print("Creating/updating 'audit_log' table (Schema v8)...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS audit_log (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            ts      TEXT    DEFAULT CURRENT_TIMESTAMP,
            event   TEXT    NOT NULL,
            user_id TEXT,
            ip      TEXT,
            detail  TEXT
        )
    ''')
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_log(ts DESC)'
    )
    cursor.execute(
        'CREATE INDEX IF NOT EXISTS idx_audit_user    ON audit_log(user_id)'
    )
    conn.commit()
    print("Schema v8 (audit_log) initialized!")

except sqlite3.Error as e:
    print(f"Error creating database: {e}")

finally:
    if conn:
        conn.close()