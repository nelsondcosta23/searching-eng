import sqlite3
import os

# Define the path for the database file (inside the persistent folder)
# When running inside Docker, this points to /app/database/vagas.db
db_path = os.path.join('database', 'vagas.db')

print(f"Connecting to database at: {db_path}...")

try:
    conn = sqlite3.connect(db_path)
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
            data_ultima_verificacao   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
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
        ("salario",           "TEXT"),
        ("tipo_contrato",     "TEXT"),
        ("nivel_experiencia", "TEXT"),
        ("relevance_score",   "INTEGER"),
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
            locations           TEXT,
            is_remote           INTEGER DEFAULT 0,
            min_salary          INTEGER DEFAULT 0,
            experience_levels   TEXT,
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
        ("callback_url",        "TEXT"),
        ("last_webhook_sent",   "TIMESTAMP"),
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

except sqlite3.Error as e:
    print(f"Error creating database: {e}")

finally:
    if conn:
        conn.close()