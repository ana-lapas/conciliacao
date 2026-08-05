import logging
from sqlalchemy import text
from app.services.sofia_api import SofiaAPI
from app.services.cache_sync import SessionLocal

logger = logging.getLogger(__name__)

def verificar_divergencia_boleto_responsavel(api: SofiaAPI, id_aluno_sophia: int):
    """
    Consulta os lançamentos e boletos de um aluno no Sofia,
    compara o nome do pagador do boleto com o responsável financeiro cadastrado localmente.
    """
    session = SessionLocal()
    resultados = []

    try:
        # 1. Recupera o aluno e seu responsável financeiro da base local (cache)
        aluno_local = session.execute(
            text("""
                SELECT s.id, s.nome as nome_aluno, sr.nome as nome_responsavel
                FROM student s
                LEFT JOIN student_responsible sr ON sr.student_id = s.id AND sr.responsavel_financeiro = TRUE
                WHERE s.sophia_id = :sophia_id
            """),
            {"sophia_id": id_aluno_sophia}
        ).first()

        if not aluno_local:
            logger.warning(f"Aluno com sophia_id {id_aluno_sophia} não encontrado na base local.")
            return []

        nome_responsavel_cadastrado = (aluno_local.nome_responsavel or "").strip().upper()

        # 2. Consome o endpoint de lançamentos do Sofia
        lancamentos = api.obter_lancamentos(id_aluno_sophia)

        for lanc in lancamentos:
            codigo_boleto = lanc.get("codigoBoleto")
            if not codigo_boleto:
                continue

            # 3. Consome o endpoint de boletos do Sofia para obter os detalhes (pagador)
            try:
                dados_boleto = api.obter_boleto(id_aluno_sophia, codigo_boleto)
                
                # Extrai o nome do pagador do retorno do boleto (ajustando conforme a estrutura exata do JSON)
                nome_pagador_boleto = ""
                if isinstance(dados_boleto, dict):
                    nome_pagador_boleto = dados_boleto.get("nomePagador") or dados_boleto.get("pagador") or ""
                elif isinstance(dados_boleto, str):
                    nome_pagador_boleto = dados_boleto

                nome_pagador_limpo = nome_pagador_boleto.strip().upper()

                # 4. Compara se o pagador do boleto é o mesmo responsável financeiro cadastrado
                divergencia = False
                if nome_responsavel_cadastrado and nome_pagador_limpo:
                    if nome_pagador_limpo != nome_responsavel_cadastrado:
                        divergencia = True

                resultados.append({
                    "codigo_boleto": codigo_boleto,
                    "numero_lancamento": lanc.get("numeroLancamento"),
                    "descricao": lanc.get("descricao"),
                    "valor_previsto": lanc.get("valorPrevisto"),
                    "data_vencimento": lanc.get("dataVencimento"),
                    "responsavel_cadastrado": nome_responsavel_cadastrado,
                    "pagador_boleto": nome_pagador_boleto,
                    "divergencia": divergencia,
                    "url_boleto": lanc.get("urlBoletoDigital"),
                    "linha_digitavel": lanc.get("linhaDigitavel")
                })

            except Exception as e:
                logger.error(f"Erro ao buscar boleto {codigo_boleto} para o aluno {id_aluno_sophia}: {e}")

    except Exception as e:
        logger.error(f"Erro ao processar lançamentos do aluno {id_aluno_sophia}: {e}")
        raise
    finally:
        session.close()

    return resultados