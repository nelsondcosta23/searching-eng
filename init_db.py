import sqlite3
import os

# Define the path for the database file (inside the persistent folder)
# When running inside Docker, this points to /app/database/vagas.db
db_path = os.path.join('database', 'vagas.db')

print(f"Connecting to database at: {db_path}...")

try:
    # Connect to database (creates the file if it doesn't exist)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Create the 'vagas' table (Schema v4)
    print("Creating 'vagas' table (Schema v4)...")
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS vagas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            plataforma TEXT NOT NULL,
            id_externo TEXT, -- Unique platform ID (e.g., data-jk from Indeed)
            titulo TEXT NOT NULL,
            empresa TEXT,
            localizacao TEXT,
            data_publicacao TEXT,
            data_scraped TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            data_ultima_verificacao TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            link TEXT NOT NULL UNIQUE,
            descricao_completa TEXT,
            status TEXT DEFAULT 'Ativa', -- 'Ativa' or 'Expirada'
            categoria TEXT,
            status_envio INTEGER DEFAULT 0,
            recrutador_nome TEXT,
            recrutador_link TEXT,
            observacoes TEXT,
            CONSTRAINT unique_vaga_platform UNIQUE (plataforma, id_externo)
        )
    ''')

    # Save changes and close connection
    conn.commit()
    print("Database and table successfully initialized!")

except sqlite3.Error as e:
    print(f"Error creating database: {e}")

finally:
    if conn:
        conn.close()