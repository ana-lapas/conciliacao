# test_api.py
from utils.sofia_api import SofiaAPI
import os
from dotenv import load_dotenv

load_dotenv()

# Instancia a API usando variáveis de ambiente do seu .env local
api = SofiaAPI(
    base_url=os.getenv("SOFIA_BASE_URL"),
    tenant=os.getenv("SOFIA_TENANT"),
    usuario=os.getenv("SOFIA_USUARIO"),
    senha=os.getenv("SOFIA_SENHA")
)

# Teste simples de extração
alunos = api.listar_alunos(pagina=1, tamanho=1)
print(f"Dados retornados: {alunos}")