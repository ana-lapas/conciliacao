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
from app.services.cache_sync import SessionLocal 

from app.services.conta_azul_receitas import criar_receita_no_conta_azul

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Funções auxiliares
# ------------------------------------------------------------------------------

def buscar_responsaveis(session: Session, nome: Optional[str] = None, cpf: Optional[str] = None) -> List[Dict[str, Any]]:
    """Busca responsáveis financeiros no cache pelo nome e/ou CPF."""
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
        return []
    rows = session.execute(query, params).fetchall()
    return [dict(row._mapping) for row in rows]


def obter_descricao_pagamento(session: Session, nosso_numero: str) -> Optional[str]:
    """Busca as mensagens do registro tipo 2 da remessa."""
    row = session.execute(
        text("SELECT mensagem1, mensagem2, mensagem3, mensagem4 FROM remessa_mensagem WHERE nosso_numero = :nn"),
        {"nn": nosso_numero}
    ).first()
    if not row:
        return None
    partes = [row.mensagem1, row.mensagem2, row.mensagem3, row.mensagem4]
    return " | ".join(p for p in partes if p)

def registrar_conciliacao(session: Session, ret, rem, resp: dict, lanc: dict) -> None:
    """Registra pagamento como CONCILIADO, atualiza status e envia ao Conta Azul."""
    nome = resp.get('nome')
    if not nome or not nome.strip():
        nome = "NÃO INFORMADO"

    # Verifica se já existe registro para este retorno (evita duplicidade)
    existente = session.execute(
        text("SELECT id FROM payment_match WHERE retorno_id = :rid"),
        {"rid": ret.id}
    ).first()

    if existente:
        # Atualiza o existente
        session.execute(
            text("""
                UPDATE payment_match SET
                    remessa_id = :remid,
                    student_id = :sid,
                    student_responsible_id = :srid,
                    nome_responsavel = :nome_resp,
                    cpf_responsavel = :cpf_resp,
                    lancamento_codigo = :lcod,
                    valor_pago = :vpago,
                    valor_previsto = :vprev,
                    data_pagamento = :dpgto,
                    data_vencimento = :dvenc,
                    status = 'CONCILIADO',
                    mensagem = NULL
                WHERE id = :id
            """),
            {
                "remid": rem.id if rem else None,
                "sid": resp["student_id"],
                "srid": resp["student_responsible_id"],
                "nome_resp": nome,
                "cpf_resp": resp.get("cpf"),
                "lcod": lanc["codigo"],
                "vpago": float(ret.valor_pago),
                "vprev": float(lanc["valorPrevisto"]),
                "dpgto": ret.data_pagamento,
                "dvenc": lanc["dataVencimento"],
                "id": existente.id
            }
        )
    else:
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
                "nome_resp": nome,
                "cpf_resp": resp.get("cpf"),
                "lcod": lanc["codigo"],
                "vpago": float(ret.valor_pago),
                "vprev": float(lanc["valorPrevisto"]),
                "dpgto": ret.data_pagamento,
                "dvenc": lanc["dataVencimento"]
            }
        )

    # Atualiza status do retorno e remessa
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

    # Descrição da remessa
    descricao_remessa = obter_descricao_pagamento(session, ret.nosso_numero)
    if descricao_remessa:
        session.execute(
            text("UPDATE payment_match SET descricao_pagamento = :desc WHERE retorno_id = :rid"),
            {"desc": descricao_remessa, "rid": ret.id}
        )

    # Envio ao Conta Azul
    try:
        descricao_completa = f"{descricao_remessa or 'Boleto'} - Aluno: {resp['nome']}"
        receita = criar_receita_no_conta_azul(
            data_vencimento=ret.data_pagamento.strftime('%Y-%m-%d'),
            valor=float(ret.valor_pago),
            descricao=descricao_completa,
            nome_cliente=resp["nome"]
        )
        session.execute(
            text("UPDATE payment_match SET conta_azul_receita_id = :caid WHERE retorno_id = :rid"),
            {"caid": receita["id"], "rid": ret.id}
        )
        logger.info(f"Receita Conta Azul criada (ID: {receita['id']})")
    except Exception as e:
        logger.error(f"Falha ao enviar para Conta Azul: {e}")
        session.execute(
            text("UPDATE payment_match SET mensagem = :msg WHERE retorno_id = :rid"),
            {"msg": f"Erro Conta Azul: {str(e)[:200]}", "rid": ret.id}
        )

def registrar_pendente_revisao(session: Session, ret, mensagem: str, nome_pagador: Optional[str] = None) -> None:
    """Marca o retorno como PENDENTE_REVISAO e insere/atualiza em payment_match."""
    nome = nome_pagador or f"PAGADOR NÃO IDENTIFICADO – Nosso Número {ret.nosso_numero}"

    existente = session.execute(
        text("SELECT id FROM payment_match WHERE retorno_id = :rid"),
        {"rid": ret.id}
    ).first()

    if existente:
        session.execute(
            text("UPDATE payment_match SET nome_responsavel = :nome, status = 'PENDENTE_REVISAO', mensagem = :msg WHERE id = :id"),
            {"nome": nome, "msg": mensagem, "id": existente.id}
        )
    else:
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
    api: SofiaAPI, student_id: int, nosso_numero: str,
    valor_pago: float, data_pagamento: str
) -> Optional[Dict[str, Any]]:
    """Busca o lançamento no Sophia que corresponde ao pagamento."""
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
    """Varre alunos do cache para achar o lançamento pelo numeroBoleto."""
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
            # 1. Remessa obrigatória
            rem = session.execute(
                text("SELECT * FROM remessa WHERE nosso_numero = :nn"),
                {"nn": ret.nosso_numero}
            ).first()

            if not rem:
                registrar_pendente_revisao(
                    session, ret,
                    "Remessa não encontrada para este título (obrigatória)."
                )
                continue

            nome_busca = rem.nome_pagador
            cpf_busca = rem.cpf_pagador

            # 2. Nome inválido (vazio ou apenas dígitos)
            if not nome_busca or nome_busca.isdigit():
                student_id, _ = encontrar_aluno_por_nosso_numero(api, session, ret.nosso_numero)
                if student_id:
                    registrar_pendente_revisao(
                        session, ret,
                        "Nome do pagador ausente na remessa, mas aluno localizado via busca reversa. Informar nome manualmente.",
                        nome_pagador=None
                    )
                else:
                    registrar_pendente_revisao(
                        session, ret,
                        "Nome do pagador ausente na remessa e busca reversa não encontrou o título."
                    )
                continue

            # 3. Buscar responsáveis no cache
            resp_rows = buscar_responsaveis(session, nome=nome_busca, cpf=cpf_busca)
            if not resp_rows:
                registrar_pendente_revisao(
                    session, ret,
                    f"Responsável '{nome_busca}' não encontrado no cache.",
                    nome_pagador=nome_busca
                )
                continue

            # 4. Tentar match com cada responsável
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
                registrar_conciliacao(session, ret, rem, match_resp, match_lanc)
            else:
                registrar_pendente_revisao(
                    session, ret,
                    "Lançamento não encontrado para o responsável.",
                    nome_pagador=nome_busca
                )

        session.commit()
        logger.info("Conciliação concluída e transação confirmada.")
    except Exception:
        session.rollback()
        logger.exception("Erro durante a conciliação. Rollback efetuado.")
        raise
    finally:
        session.close()