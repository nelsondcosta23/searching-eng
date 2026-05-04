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

    conn.commit()
    print("User profile schema successfully initialized!")

except sqlite3.Error as e:
    print(f"Error creating database: {e}")

finally:
    if conn:
        conn.close()