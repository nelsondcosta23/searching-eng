import sqlite3
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Define database path targeting database/intelligence.db by default
db_path = os.environ.get('DB_PATH', os.path.join(os.path.dirname(os.path.abspath(__file__)), 'database', 'vagas.db'))

# Ensure parent directories exist
os.makedirs(os.path.dirname(db_path), exist_ok=True)

print(f"Connecting to database at: {db_path}...")

conn = None
try:
    conn = sqlite3.connect(db_path)
    conn.execute('PRAGMA journal_mode=WAL;')
    cursor = conn.cursor()

    # Create the 'jobs' table
    print("Creating 'jobs' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS jobs (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            plataforma           TEXT NOT NULL,
            id_externo           TEXT,
            titulo               TEXT NOT NULL,
            empresa              TEXT NOT NULL,
            localizacao          TEXT,
            link                 TEXT NOT NULL UNIQUE,
            data_publicacao      TEXT,
            data_scraped         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            posting_age_days     INTEGER,
            salario              TEXT,
            tipo_contrato        TEXT,
            nivel_experiencia    TEXT,
            work_mode            TEXT,
            job_type             TEXT,
            descricao            TEXT,
            observacoes          TEXT,
            recrutador_nome      TEXT,
            recrutador_link      TEXT,
            status               TEXT DEFAULT 'Ativa',
            normalized_country   TEXT,
            CONSTRAINT unique_job_platform UNIQUE (plataforma, id_externo)
        )
    ''')

    # Safe migration: Alter jobs table to add work_mode if migrating an existing DB
    try:
        cursor.execute("ALTER TABLE jobs ADD COLUMN work_mode TEXT")
        print("  Migration: Added 'work_mode' column to 'jobs' table.")
    except sqlite3.OperationalError:
        pass

    # Create the 'job_skills' table for relational tech skills mapping
    print("Creating 'job_skills' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS job_skills (
            job_id               INTEGER NOT NULL,
            skill                TEXT NOT NULL,
            PRIMARY KEY (job_id, skill),
            FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
        )
    ''')

    # Create the 'companies' table for inception/age caching
    print("Creating 'companies' table...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS companies (
            name                 TEXT PRIMARY KEY,
            inception_year       INTEGER,
            company_age          INTEGER,
            description          TEXT,
            wikidata_qid         TEXT,
            last_updated         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Create performance indexes
    print("Creating performance indexes...")
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_plataforma ON jobs(plataforma)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_empresa ON jobs(empresa)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_job_type ON jobs(job_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_data_scraped ON jobs(data_scraped)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_job_skills_skill ON job_skills(skill)')

    conn.commit()
    print("Database successfully initialized!")

except sqlite3.Error as e:
    print(f"Error creating database: {e}")

finally:
    if conn:
        conn.close()