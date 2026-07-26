# services/matcher.py
"""
Módulo de conciliação entre retorno bancário (CNAB 400), remessa e Sophia.

Fluxo:
1. Para cada retorno pendente, busca a remessa correspondente.
2. Identifica o responsável financeiro no cache local (student_responsible).
3. Obtém os lançamentos do aluno no Sophia e encontra o lançamento quitado.
4. Registra o resultado em payment_match e atualiza os status.

SITUAÇÕES DE CONCILIAÇÃO (ver comentários no código):
- Situação 1: Retorno com nome do pagador válido → busca no cache e matching.
- Situação 2: Retorno sem nome, mas remessa com nome → usa nome da remessa.
- Situação 3: Sem nome algum → busca reversa pelo nosso número.
- Situação 4: Encontrado aluno e lançamento, mas sem nome do pagador real → PENDENTE_REVISAO.
"""

import logging
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.sofia_api import SofiaAPI
from app.services.cache_sync import SessionLocal  # reutiliza a fábrica de sessões

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Funções auxiliares
# ------------------------------------------------------------------------------

def buscar_responsaveis(session: Session, nome: Optional[str] = None, cpf: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Busca responsáveis financeiros no cache pelo nome e/ou CPF.
    Pelo menos um dos parâmetros deve ser fornecido.
    Retorna lista de dicionários com student_id, student_responsible_id, nome, cpf.
    """
    if cpf:
        query = text("""
            SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
            FROM student_responsible sr
            WHERE sr.cpf = :cpf AND sr.responsavel_financeiro = true
        """)
        params = {"cpf": cpf}
    elif nome:
        query = text("""
            SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
            FROM student_responsible sr
            WHERE sr.nome ILIKE :nome AND sr.responsavel_financeiro = true
        """)
        params = {"nome": f"%{nome}%"}
    else:
        return []  # sem critério

    rows = session.execute(query, params).fetchall()
    return [dict(row._mapping) for row in rows]


def registrar_conciliacao(session: Session, ret, rem, resp: dict, lanc: dict) -> None:
    """
    Registra pagamento como CONCILIADO.
    Atualiza retorno e remessa (se existir) para CONCILIADO/PAGO.
    O nome do pagador vem de resp['nome'] (que é o nome do cache, já em maiúsculas,
    que deve corresponder ao nome da remessa/retorno validado).
    """
    session.execute(
        text("""
            INSERT INTO payment_match (
                retorno_id, remessa_id, student_id, student_responsible_id,
                nome_responsavel, cpf_responsavel,
                lancamento_codigo, valor_pago, valor_previsto,
                data_pagamento, data_vencimento, status
            ) VALUES (
                :rid, :remid, :sid, :srid,
                :nome_resp, :cpf_resp,
                :lcod, :vpago, :vprev,
                :dpgto, :dvenc, 'CONCILIADO'
            )
        """),
        {
            "rid": ret.id,
            "remid": rem.id if rem else None,
            "sid": resp["student_id"],
            "srid": resp["student_responsible_id"],
            "nome_resp": resp["nome"],
            "cpf_resp": resp.get("cpf"),
            "lcod": lanc["codigo"],
            "vpago": float(ret.valor_pago),
            "vprev": float(lanc["valorPrevisto"]),
            "dpgto": ret.data_pagamento,
            "dvenc": lanc["dataVencimento"]
        }
    )
    session.execute(
        text("UPDATE retorno SET status = 'CONCILIADO' WHERE id = :rid"),
        {"rid": ret.id}
    )
    if rem:
        session.execute(
            text("UPDATE remessa SET status = 'PAGO' WHERE id = :remid"),
            {"remid": rem.id}
        )
    logger.info(f"Retorno {ret.id}: CONCILIADO (nosso número {ret.nosso_numero})")


def registrar_pendente_revisao(session: Session, ret, mensagem: str, nome_pagador: Optional[str] = None) -> None:
    """
    Marca o retorno como PENDENTE_REVISAO e insere em payment_match.
    O nome_pagador, se fornecido, será usado como nome do pagador (placeholder ou nome extraído).
    """
    nome = nome_pagador or f"PAGADOR NÃO IDENTIFICADO – Nosso Número {ret.nosso_numero}"
    session.execute(
        text("""
            INSERT INTO payment_match (retorno_id, nome_responsavel, status, mensagem)
            VALUES (:rid, :nome, 'PENDENTE_REVISAO', :msg)
        """),
        {"rid": ret.id, "nome": nome, "msg": mensagem}
    )
    session.execute(
        text("UPDATE retorno SET status = 'PENDENTE_REVISAO' WHERE id = :rid"),
        {"rid": ret.id}
    )
    logger.info(f"Retorno {ret.id}: PENDENTE_REVISAO - {mensagem}")


def encontrar_lancamento(
    api: SofiaAPI,
    student_id: int,
    nosso_numero: str,
    valor_pago: float,
    data_pagamento: str
) -> Optional[Dict[str, Any]]:
    """
    Busca o lançamento no Sophia que corresponde ao pagamento.

    Critérios (em ordem):
    1. numeroBoleto igual ao nosso_numero e recebido == 0.
    2. Valor exato e data de vencimento próxima (tolerância de 5 dias).
    Retorna o dicionário do lançamento ou None.
    """
    try:
        lancamentos = api.obter_lancamentos(student_id)
    except Exception as e:
        logger.error(f"Erro ao obter lançamentos do aluno {student_id}: {e}")
        return None

    data_pgto = datetime.strptime(data_pagamento, '%Y-%m-%d')
    melhor_lancamento = None
    melhor_diferenca = timedelta.max

    for lanc in lancamentos:
        if lanc.get("recebido") != 0:
            continue

        if str(lanc.get("numeroBoleto", "")) == nosso_numero:
            logger.debug(f"Match por nosso número: {nosso_numero}")
            return lanc

        try:
            valor_previsto = float(lanc.get("valorPrevisto", 0))
        except (TypeError, ValueError):
            continue

        if abs(valor_previsto - valor_pago) > 0.01:
            continue

        try:
            data_venc = datetime.strptime(lanc.get("dataVencimento", ""), '%Y-%m-%d')
        except (ValueError, TypeError):
            continue

        diferenca = abs((data_venc - data_pgto).days)
        if diferenca <= 5 and diferenca < melhor_diferenca:
            melhor_diferenca = diferenca
            melhor_lancamento = lanc

    if melhor_lancamento:
        logger.debug(f"Match por valor/data: dif={melhor_diferenca} dias")
    return melhor_lancamento


def encontrar_aluno_por_nosso_numero(api: SofiaAPI, session: Session, nosso_numero: str):
    """
    Varre todos os alunos do cache e busca nos lançamentos o numeroBoleto.
    Retorna (student_id, responsavelFinanceiro) ou (None, None).
    """
    alunos = session.execute(text("SELECT sophia_id FROM student")).fetchall()
    for (sophia_id,) in alunos:
        try:
            lancamentos = api.obter_lancamentos(sophia_id)
        except Exception:
            continue
        for lanc in lancamentos:
            if str(lanc.get("numeroBoleto")) == nosso_numero:
                return sophia_id, lanc.get("responsavelFinanceiro")
    return None, None


# ------------------------------------------------------------------------------
# Função principal de conciliação
# ------------------------------------------------------------------------------

def conciliar_retorno(api: SofiaAPI) -> None:
    session = SessionLocal()
    try:
        retornos = session.execute(
            text("SELECT * FROM retorno WHERE status = 'PENDENTE'")
        ).fetchall()

        if not retornos:
            logger.info("Nenhum retorno pendente para conciliar.")
            return

        logger.info(f"Iniciando conciliação de {len(retornos)} retornos...")

        for ret in retornos:
            # 1. Obter remessa associada (se existir)
            rem = session.execute(
                text("SELECT * FROM remessa WHERE nosso_numero = :nn"),
                {"nn": ret.nosso_numero}
            ).first()

            # ----------------------------------------------------------------
            # SITUAÇÃO 1: Retorno tem nome de pagador válido (texto)
            # ----------------------------------------------------------------
            if ret.nome_pagador and not ret.nome_pagador.isdigit():
                nome_busca = ret.nome_pagador
                cpf_busca = ret.cpf_pagador  # pode ser None
                resp_rows = buscar_responsaveis(session, nome=nome_busca, cpf=cpf_busca)

                if resp_rows:
                    # Tenta match com cada responsável
                    match_resp = None
                    match_lanc = None
                    for resp in resp_rows:
                        lanc = encontrar_lancamento(
                            api, resp["student_id"], ret.nosso_numero,
                            float(ret.valor_pago), ret.data_pagamento.isoformat()
                        )
                        if lanc:
                            match_resp = resp
                            match_lanc = lanc
                            break

                    if match_resp and match_lanc:
                        # Conciliado automático (nome do retorno é válido e encontrado no cache)
                        registrar_conciliacao(session, ret, rem, match_resp, match_lanc)
                        continue
                    else:
                        # Responsável encontrado, mas lançamento não localizado
                        registrar_pendente_revisao(
                            session, ret,
                            "Lançamento não encontrado para o responsável",
                            nome_pagador=nome_busca
                        )
                        continue
                else:
                    # Nome não existe no cache -> PENDENTE_REVISAO
                    registrar_pendente_revisao(
                        session, ret,
                        f"Responsável '{nome_busca}' não encontrado no cache",
                        nome_pagador=nome_busca
                    )
                    continue

            # ----------------------------------------------------------------
            # SITUAÇÃO 2: Retorno sem nome, mas remessa tem nome válido
            # ----------------------------------------------------------------
            if rem and rem.nome_pagador and not rem.nome_pagador.isdigit():
                nome_busca = rem.nome_pagador
                cpf_busca = rem.cpf_pagador
                resp_rows = buscar_responsaveis(session, nome=nome_busca, cpf=cpf_busca)

                if resp_rows:
                    match_resp = None
                    match_lanc = None
                    for resp in resp_rows:
                        lanc = encontrar_lancamento(
                            api, resp["student_id"], ret.nosso_numero,
                            float(ret.valor_pago), ret.data_pagamento.isoformat()
                        )
                        if lanc:
                            match_resp = resp
                            match_lanc = lanc
                            break

                    if match_resp and match_lanc:
                        # Nome veio da remessa, mas foi validado no cache
                        registrar_conciliacao(session, ret, rem, match_resp, match_lanc)
                        continue
                    else:
                        registrar_pendente_revisao(
                            session, ret,
                            "Lançamento não encontrado (nome da remessa)",
                            nome_pagador=nome_busca
                        )
                        continue
                else:
                    # Nome da remessa não está no cache
                    registrar_pendente_revisao(
                        session, ret,
                        f"Responsável da remessa '{nome_busca}' não encontrado no cache",
                        nome_pagador=nome_busca
                    )
                    continue

            # ----------------------------------------------------------------
            # SITUAÇÃO 3: Nenhum nome válido (nem retorno, nem remessa) → busca reversa
            # ----------------------------------------------------------------
            student_id, nome_responsavel = encontrar_aluno_por_nosso_numero(api, session, ret.nosso_numero)
            if student_id:
                # Aluno encontrado, mas NÃO temos o nome real do pagador.
                # Fica PENDENTE_REVISAO até que a secretária informe o nome.
                registrar_pendente_revisao(
                    session, ret,
                    "Aluno localizado via busca reversa, mas nome do pagador ausente. Informar manualmente.",
                    nome_pagador=None  # placeholder será gerado
                )
                continue
            else:
                # Busca reversa falhou
                registrar_pendente_revisao(
                    session, ret,
                    "Nenhum nome disponível e busca reversa não encontrou o título."
                )
                continue

        session.commit()
        logger.info("Conciliação concluída e transação confirmada.")
    except Exception:
        session.rollback()
        logger.exception("Erro durante a conciliação. Rollback efetuado.")
        raise
    finally:
        session.close()