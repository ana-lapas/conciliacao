# services/remessa_sync.py
import logging
import sys
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.services.remessa_reader import RemessaReader
import os

load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.critical("DATABASE_URL não definida.")
    sys.exit(1)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

def sincronizar_remessa(caminho_rem: str):
    """
    Lê um arquivo de remessa e insere/atualiza os registros e mensagens no banco.
    """
    reader = RemessaReader(caminho_rem)      # <--- CORRIGIDO: sem argumento engine
    registros = reader.processar()
    if not registros:
        logger.warning("Nenhum registro processado.")
        return

    session = SessionLocal()
    try:
        # Upsert dos títulos
        dados_insert = []
        for r in registros:
            if not r.get("nosso_numero"):
                continue
            dados_insert.append({
                "nosso_numero": r["nosso_numero"],
                "dac": r.get("dac"),
                "nome_pagador": r["nome_pagador"],
                "cpf_pagador": r.get("cpf"),
                "valor": r["valor"],
                "data_vencimento": r["vencimento"],
                "arquivo_origem": caminho_rem,
                "status": "PENDENTE"
            })
        if dados_insert:
            session.execute(
                text("""
                    INSERT INTO remessa (nosso_numero, dac, nome_pagador, cpf_pagador, valor, data_vencimento, arquivo_origem, status)
                    VALUES (:nosso_numero, :dac, :nome_pagador, :cpf_pagador, :valor, :data_vencimento, :arquivo_origem, :status)
                    ON CONFLICT (nosso_numero) DO UPDATE SET
                        dac = EXCLUDED.dac,
                        nome_pagador = EXCLUDED.nome_pagador,
                        cpf_pagador = EXCLUDED.cpf_pagador,
                        valor = EXCLUDED.valor,
                        data_vencimento = EXCLUDED.data_vencimento,
                        arquivo_origem = EXCLUDED.arquivo_origem,
                        sincronizado_em = NOW()
                """),
                dados_insert
            )
            logger.info(f"Remessa sincronizada: {len(dados_insert)} registros.")

        # Upsert das mensagens
        if reader.mensagens:
            mensagens_insert = []
            for msg in reader.mensagens:
                mensagens_insert.append({
                    "nosso_numero": msg["nosso_numero"],
                    "mensagem1": msg["mensagem1"],
                    "mensagem2": msg["mensagem2"],
                    "mensagem3": msg["mensagem3"],
                    "mensagem4": msg["mensagem4"],
                })
            session.execute(
                text("""
                    INSERT INTO remessa_mensagem (nosso_numero, mensagem1, mensagem2, mensagem3, mensagem4)
                    VALUES (:nosso_numero, :mensagem1, :mensagem2, :mensagem3, :mensagem4)
                    ON CONFLICT (nosso_numero) DO UPDATE SET
                        mensagem1 = EXCLUDED.mensagem1,
                        mensagem2 = EXCLUDED.mensagem2,
                        mensagem3 = EXCLUDED.mensagem3,
                        mensagem4 = EXCLUDED.mensagem4
                """),
                mensagens_insert
            )
            logger.info(f"Mensagens sincronizadas: {len(mensagens_insert)} registros.")

        session.commit()
    except Exception:
        session.rollback()
        logger.exception("Erro ao sincronizar remessa.")
        raise
    finally:
        session.close()