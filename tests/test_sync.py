# tests/test_sync.py
import logging
import sys
import os
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from app.services.cache_sync import sync_students
# Carrega o .env
load_dotenv(find_dotenv())

# Importa a função de sincronização

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("test_sync")

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.critical("DATABASE_URL não definida.")
    sys.exit(1)

def main():
    # 1. Executa a sincronização
    try:
        sync_students()
    except Exception as e:
        logger.critical(f"Sincronização falhou: {e}")
        sys.exit(1)

    # 2. Conecta ao banco para verificar
    engine = create_engine(DATABASE_URL, echo=False)
    with engine.connect() as conn:
        # Contagem de alunos
        total = conn.execute(text("SELECT COUNT(*) FROM student")).scalar()
        print(f"\nTotal de alunos no banco: {total}")

        # Contagem de responsáveis financeiros
        resp_count = conn.execute(text("SELECT COUNT(*) FROM student_responsible WHERE responsavel_financeiro = true")).scalar()
        print(f"Total de responsáveis financeiros: {resp_count}")

        # Mostrar alguns exemplos
        print("\nExemplos de alunos com responsáveis:")
        rows = conn.execute(text("""
            SELECT s.nome AS aluno, sr.nome AS responsavel, sr.cpf
            FROM student s
            JOIN student_responsible sr ON sr.student_id = s.id
            ORDER BY s.nome
            LIMIT 10
        """)).fetchall()
        for row in rows:
            print(f"  Aluno: {row.aluno}  |  Responsável: {row.responsavel}  |  CPF: {row.cpf or 'N/I'}")

    engine.dispose()
    logger.info("Verificação concluída.")

if __name__ == "__main__":
    main()