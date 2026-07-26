# app/services/retorno_sync.py
import logging
import os
import sys
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from app.services.retorno_reader import RetornoReader

load_dotenv(find_dotenv())

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.critical("DATABASE_URL não definida.")
    sys.exit(1)

engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

def sincronizar_retorno(caminho_ret: str):
    """
    Lê um arquivo de retorno (.ret) e insere/atualiza os registros no banco.
    """
    reader = RetornoReader(caminho_ret)
    reader.processar()
    registros = reader.registros   # já está acessível como atributo

    if not registros:
        logger.warning("Nenhum registro de retorno encontrado.")
        return

    session = SessionLocal()
    try:
        dados_insert = []
        for reg in registros:
            dados_insert.append({
                "nosso_numero": reg["nosso_numero"],
                "nome_pagador": reg.get("nome_pagador", "").strip().upper(),
                "valor_pago": reg["valor"],
                "data_pagamento": reg["data"],
                "codigo_ocorrencia": reg["ocorrencia"],
                "arquivo_origem": caminho_ret,
                "status": "PENDENTE"
            })

        session.execute(
            text("""
                INSERT INTO retorno (nosso_numero, nome_pagador, valor_pago, data_pagamento, codigo_ocorrencia, arquivo_origem, status)
                VALUES (:nosso_numero, :nome_pagador, :valor_pago, :data_pagamento, :codigo_ocorrencia, :arquivo_origem, :status)
                ON CONFLICT (nosso_numero) DO UPDATE SET
                    nome_pagador = EXCLUDED.nome_pagador,
                    valor_pago = EXCLUDED.valor_pago,
                    data_pagamento = EXCLUDED.data_pagamento,
                    codigo_ocorrencia = EXCLUDED.codigo_ocorrencia,
                    arquivo_origem = EXCLUDED.arquivo_origem,
                    status = EXCLUDED.status
            """),
            dados_insert
        )
        session.commit()
        logger.info(f"Retorno sincronizado: {len(registros)} registros.")
    except Exception:
        session.rollback()
        logger.exception("Erro ao sincronizar retorno.")
        raise
    finally:
        session.close()