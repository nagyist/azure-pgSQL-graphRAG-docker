# Copyright (c) Microsoft Corporation.
# Licensed under the MIT license.
import pyodbc
import psycopg2
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

db_type = os.getenv("DB_TYPE", "Azure postgres")

if db_type.strip().lower() == "azure sql":
    # Azure SQL connection using your existing MY_DB_ variables
    print(pyodbc.drivers())
    conn = pyodbc.connect(
        f"DRIVER={{ODBC Driver 18 for SQL Server}};"
        f"SERVER={os.getenv('MY_DB_HOST')},1433;"
        f"DATABASE={os.getenv('MY_DB_NAME')};"
        f"UID={os.getenv('MY_DB_USER')};"
        f"PWD={os.getenv('MY_DB_PASSWORD')};"
        "Encrypt=yes;"
        "TrustServerCertificate=no;"
        "Connection Timeout=30;"
    )

elif db_type.strip().lower() == "azure postgres":
    # PostgreSQL connection
    conn = psycopg2.connect(
        host=os.getenv("POSTGRES_HOST"),
        port=os.getenv("POSTGRES_PORT"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
        dbname=os.getenv("POSTGRES_DB"),
        sslmode='require'  # Required for Azure PostgreSQL
    )

else:
    raise ValueError(f"Unsupported DB_TYPE: {db_type}")

cursor = conn.cursor()

# Create the table if it doesn't exist
cursor.execute("""
IF NOT EXISTS (
    SELECT * FROM INFORMATION_SCHEMA.TABLES 
    WHERE TABLE_NAME = 'graphrag_inputs'
)
BEGIN
    CREATE TABLE graphrag_inputs (
        id INT IDENTITY(1,1) PRIMARY KEY,
        prompt_text NVARCHAR(MAX) NOT NULL
    );
END
""")
conn.commit()

# Directory containing your .txt files
input_dir = "C:/Users/helenzeng/graphRAG2/postgreSQL-AGE/data/input"

# Insert each .txt file's content into the table
for filename in os.listdir(input_dir):
    if filename.endswith(".txt"):
        file_path = os.path.join(input_dir, filename)
        with open(file_path, 'r', encoding='utf-8') as file:
            content = file.read()
            cursor.execute("INSERT INTO graphrag_inputs (prompt_text) VALUES (?);", (content,))
            conn.commit()

        print(f"Processed file: {filename}")
        print(f"Number of lines in {filename}: {len(content.splitlines())}")

cursor.close()
conn.close()
print("All .txt files inserted into", db_type, "Database.")
