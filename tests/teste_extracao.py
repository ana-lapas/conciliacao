import logging
import json
import sys
import os
from dotenv import load_dotenv, find_dotenv
from datetime import datetime
from app.services.sofia_api import SofiaAPI  

# Procura automaticamente o arquivo .env a partir do diretório atual ou acima
load_dotenv(find_dotenv())

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("teste_extracao")

# Agora as credenciais vêm do ambiente
BASE_URL = os.getenv("SOFIA_BASE_URL")
TENANT   = os.getenv("SOFIA_TENANT")
USUARIO  = os.getenv("SOFIA_USUARIO")
SENHA    = os.getenv("SOFIA_SENHA")

# Validação rápida
if not all([BASE_URL, TENANT, USUARIO, SENHA]):
    logger.critical("Variáveis de ambiente não definidas. Verifique o arquivo .env.")
    sys.exit(1)

def listar_todos_alunos(api: SofiaAPI) -> list:
    """Coleta todos os alunos paginando até o fim."""
    todos = []
    pagina = 1
    while True:
        logger.info(f"Página {pagina}...")
        try:
            dados = api.listar_alunos(pagina=pagina, tamanho=50)
        except Exception as e:
            logger.error(f"Falha na página {pagina}: {e}")
            break

        if not dados:
            logger.info("Página vazia – fim da listagem.")
            break

        logger.info(f"Página {pagina}: {len(dados)} alunos recebidos.")
        todos.extend(dados)   # <--- LINHA FALTANTE
        pagina += 1

    return todos

def analisar_responsaveis(alunos: list) -> dict:
    """Analisa responsáveis financeiros e gera estatísticas."""
    stats = {
        "total_alunos": len(alunos),
        "alunos_sem_resp_fin": 0,
        "total_resp_fin": 0,
        "alunos_com_multiplos_resp": 0
    }
    alunos_sem = []
    for aluno in alunos:
        nome = aluno.get("nome", "N/D")
        cod = aluno.get("codigo")
        responsaveis = aluno.get("responsaveis", [])
        resp_fin = [r for r in responsaveis if r.get("responsavelFinanceiro") is True]

        if not resp_fin:
            logger.warning(f"Aluno {cod} - {nome}: sem responsável financeiro.")
            stats["alunos_sem_resp_fin"] += 1
            alunos_sem.append(cod)
        else:
            stats["total_resp_fin"] += len(resp_fin)
            if len(resp_fin) > 1:
                logger.info(f"Aluno {cod} - {nome}: {len(resp_fin)} resp. financeiros.")
                stats["alunos_com_multiplos_resp"] += 1
            for r in resp_fin:
                logger.debug(f"  -> {r['nome']} (CPF: {r.get('cpf', 'N/I')})")

    stats["alunos_sem_lista"] = alunos_sem
    return stats

def main():
    api = SofiaAPI(BASE_URL, TENANT, USUARIO, SENHA)
    try:
        api.autenticar()
        logger.info("Autenticação OK.")
    except Exception as e:
        logger.critical(f"Autenticação falhou: {e}")
        sys.exit(1)

    logger.info("Iniciando extração completa de alunos...")
    alunos = listar_todos_alunos(api)
    logger.info(f"Extração concluída. Total: {len(alunos)} alunos.")

    if not alunos:
        logger.error("Nenhum aluno retornado. Verifique credenciais/tenant.")
        sys.exit(1)

    # Análise
    stats = analisar_responsaveis(alunos)
    print("\n" + "="*60)
    print(f"Total de alunos: {stats['total_alunos']}")
    print(f"Alunos sem resp. financeiro: {stats['alunos_sem_resp_fin']}")
    if stats['alunos_sem_lista']:
        print(f"  Códigos: {stats['alunos_sem_lista']}")
    print(f"Total de resp. financeiros: {stats['total_resp_fin']}")
    print(f"Alunos com múltiplos resp. fin.: {stats['alunos_com_multiplos_resp']}")
    print("="*60)

    # Salva dump
    arquivo_dump = f"dump_alunos_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(arquivo_dump, "w", encoding="utf-8") as f:
        json.dump(alunos, f, indent=2, ensure_ascii=False)
    logger.info(f"Dump salvo em '{arquivo_dump}'.")

    api.close()

if __name__ == "__main__":
    main()