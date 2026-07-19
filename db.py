import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")

supabase = create_client(url, key)

# Exemplo de como inserir um dado lido do seu arquivo .rem
def salvar_leitura(data_processada):
    return supabase.table("leituras").insert(data_processada).execute()