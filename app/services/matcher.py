# services/matcher.py
"""
Módulo de Conciliação Financeira Automatizada (Bradesco CNAB 400).
"""

import logging
import re
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

from requests import session
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.sofia_api import SofiaAPI
from app.services.cache_sync import SessionLocal 
from app.services.conta_azul_receitas import criar_receita_com_baixa

logger = logging.getLogger(__name__)

# ==============================================================================
# SANITIZAÇÃO DE DADOS
# ==============================================================================

def normalizar_texto(texto: str) -> str:
    """
    Padroniza strings de texto para comparação segura em caixa alta com trim.
    """
    if not texto:
        return ""
    return " ".join(texto.strip().upper().split())


# ==============================================================================
# CONSULTAS DE BANCO DE DADOS E LOOKUPS DE DE-PARA
# ==============================================================================

def buscar_responsaveis(session: Session, nome: str, cpf: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Localiza os responsáveis financeiros na tabela local por CPF ou Nome.
    """
    nome_limpo = normalizar_texto(nome)

    # 1. Busca por CPF (obrigatório se disponível)
    if cpf:
        cpf_limpo = re.sub(r'\D', '', str(cpf))
        rows = session.execute(
            text("""
                SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
                FROM student_responsible sr
                WHERE REPLACE(REPLACE(REPLACE(sr.cpf, '.', ''), '-', ''), ' ', '') = :cpf
                  AND sr.responsavel_financeiro = true
            """),
            {"cpf": cpf_limpo}
        ).fetchall()
        if rows:
            return [dict(row._mapping) for row in rows]
        # Se o CPF não foi encontrado, NÃO fazemos fallback para nome – retornamos vazio.
        return []

    # 2. Busca por nome EXATO (normalizado)
    rows = session.execute(
        text("""
            SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
            FROM student_responsible sr
            WHERE UPPER(sr.nome) = :nome
              AND sr.responsavel_financeiro = true
        """),
        {"nome": nome_limpo}
    ).fetchall()
    return [dict(row._mapping) for row in rows]


# ==============================================================================
# SANITIZAÇÃO DE DADOS
# ==============================================================================

def normalizar_nosso_numero(nn: str) -> str:
    """Remove zeros à esquerda e caracteres não numéricos."""
    if not nn:
        return ""
    apenas_numeros = re.sub(r'\D', '', str(nn))
    return apenas_numeros.lstrip('0')


def normalizar_texto(texto: str) -> str:
    """Padroniza strings para caixa alta e sem espaços extras."""
    if not texto:
        return ""
    return " ".join(texto.strip().upper().split())


# ==============================================================================
# CONSULTAS DE BANCO DE DADOS
# ==============================================================================
def buscar_responsaveis(session: Session, nome: str, cpf: Optional[str] = None) -> List[Dict[str, Any]]:
    """Localiza responsáveis financeiros por CPF ou nome exato, retornando o sophia_id do aluno."""
    nome_limpo = normalizar_texto(nome)

    if cpf:
        cpf_limpo = re.sub(r'\D', '', str(cpf))
        rows = session.execute(
            text("""
                SELECT sr.student_id, sr.id AS student_responsible_id,
                       s.sophia_id AS student_sophia_id, sr.nome, sr.cpf
                FROM student_responsible sr
                JOIN student s ON s.id = sr.student_id
                WHERE REPLACE(REPLACE(REPLACE(sr.cpf, '.', ''), '-', ''), ' ', '') = :cpf
                  AND sr.responsavel_financeiro = true
            """),
            {"cpf": cpf_limpo}
        ).fetchall()
        if rows:
            return [dict(row._mapping) for row in rows]
        return []

    rows = session.execute(
        text("""
            SELECT sr.student_id, sr.id AS student_responsible_id,
                   s.sophia_id AS student_sophia_id, sr.nome, sr.cpf
            FROM student_responsible sr
            JOIN student s ON s.id = sr.student_id
            WHERE UPPER(sr.nome) = :nome
              AND sr.responsavel_financeiro = true
        """),
        {"nome": nome_limpo}
    ).fetchall()
    return [dict(row._mapping) for row in rows]


def obter_remessa_por_nosso_numero(session: Session, nosso_numero: str, dac: Optional[str] = None):
    """Busca a Remessa pelo Nosso Número e opcionalmente DAC."""
    nn_limpo = normalizar_nosso_numero(nosso_numero)

    if dac:
        query = text("""
            SELECT * FROM remessa
            WHERE (LTRIM(nosso_numero, '0') = :nn_limpo OR nosso_numero = :nn_raw)
              AND (dac = :dac OR dac IS NULL)
            LIMIT 1
        """)
        res = session.execute(query, {"nn_limpo": nn_limpo, "nn_raw": nosso_numero, "dac": dac}).first()
        if res:
            return res

    query_fallback = text("""
        SELECT * FROM remessa
        WHERE LTRIM(nosso_numero, '0') = :nn_limpo
           OR nosso_numero = :nn_raw
        LIMIT 1
    """)
    return session.execute(query_fallback, {"nn_limpo": nn_limpo, "nn_raw": nosso_numero}).first()


def obter_descricao_pagamento(session: Session, nosso_numero: str) -> Optional[str]:
    """Recupera as mensagens do Registro Tipo 2."""
    nn_limpo = normalizar_nosso_numero(nosso_numero)
    row = session.execute(
        text("""
            SELECT mensagem1, mensagem2, mensagem3, mensagem4
            FROM remessa_mensagem
            WHERE LTRIM(nosso_numero, '0') = :nn_limpo OR nosso_numero = :nn_raw
            LIMIT 1
        """),
        {"nn_limpo": nn_limpo, "nn_raw": nosso_numero}
    ).first()

    if not row:
        return None

    partes = [row.mensagem1, row.mensagem2, row.mensagem3, row.mensagem4]
    return " | ".join(p.strip() for p in partes if p and p.strip())


# ==============================================================================
# LÓGICA DE MATCHING FINANCEIRO
# ==============================================================================

def encontrar_lancamento(
    api: SofiaAPI, student_id: int, nosso_numero: str,
    valor_pago: float, data_pagamento: Any
) -> Optional[Dict[str, Any]]:
    """Valida o lançamento em aberto cruzando Nosso Número e tolerância de valor/vencimento."""
    try:
        lancamentos = api.obter_lancamentos(student_id)
    except Exception as e:
        logger.error(f"Erro ao obter lançamentos do aluno {student_id}: {e}")
        return None

    if isinstance(data_pagamento, datetime):
        data_pgto = data_pagamento
    else:
        data_pgto = datetime.strptime(str(data_pagamento)[:10], '%Y-%m-%d')

    nn_limpo = normalizar_nosso_numero(nosso_numero)
    melhor_lancamento = None
    melhor_diferenca = timedelta.max

    for lanc in lancamentos:
        # 1. Match pelo Nosso Número
        num_boleto_api = normalizar_nosso_numero(str(lanc.get("numeroBoleto", "")))
        if num_boleto_api and num_boleto_api == nn_limpo:
            return lanc

        # 2. Match por Valor (tolerância de R$ 0,01)
        try:
            valor_previsto = float(lanc.get("valorPrevisto", 0))
        except (TypeError, ValueError):
            continue

        if abs(valor_previsto - valor_pago) > 50.5:
            continue

        # 3. Match por Proximidade de Vencimento (janela de 5 dias)
        try:
            data_venc = datetime.strptime(str(lanc.get("dataVencimento", ""))[:10], '%Y-%m-%d')
        except (ValueError, TypeError):
            continue

        diferenca = abs((data_venc - data_pgto).days)
        if diferenca <= 15 and diferenca < melhor_diferenca:
            melhor_diferenca = diferenca
            melhor_lancamento = lanc

    if not melhor_lancamento:
        logger.warning(
            f"Nenhum lançamento casou para aluno {student_id}. "
            f"Nosso número buscado: {nn_limpo}. "
            f"Valor pago: {valor_pago}. Data pgto: {data_pgto.date()}"
        )
        # Opcional: logar os primeiros lançamentos para inspeção
        for lanc in lancamentos[:3]:
            logger.debug(
                f"  Lançamento {lanc.get('codigo')}: "
                f"numeroBoleto={lanc.get('numeroBoleto')}, "
                f"valorPrevisto={lanc.get('valorPrevisto')}, "
                f"dataVencimento={lanc.get('dataVencimento')}"
            )
    return melhor_lancamento


# ==============================================================================
# PERSISTÊNCIA E INTEGRAÇÃO
# ==============================================================================

def registrar_conciliacao(session: Session, ret, rem, resp: dict, lanc: dict) -> None:
    """Efetiva a conciliação localmente e dispara a baixa no Conta Azul."""
    nome = normalizar_texto(resp.get('nome')) or "NÃO INFORMADO"

    existente = session.execute(
        text("SELECT id FROM payment_match WHERE retorno_id = :rid"),
        {"rid": ret.id}
    ).first()

    params_match = {
        "remid": rem.id if rem else None,
        "sid": resp["student_id"],
        "srid": resp["student_responsible_id"],
        "nome_resp": nome,
        "cpf_resp": resp.get("cpf"),
        "lcod": lanc["codigo"],
        "vpago": float(ret.valor_pago),
        "vprev": float(lanc.get("valorPrevisto", ret.valor_pago)),
        "dpgto": ret.data_pagamento,
        "dvenc": lanc.get("dataVencimento", ret.data_pagamento),
        "rid": ret.id,
        "dac": getattr(ret, 'dac', None) or (rem.dac if rem else None)
    }

    if existente:
        params_match["id"] = existente.id
        session.execute(
            text("""
                UPDATE payment_match SET
                    remessa_id = :remid, student_id = :sid, student_responsible_id = :srid,
                    nome_responsavel = :nome_resp, cpf_responsavel = :cpf_resp,
                    lancamento_codigo = :lcod, valor_pago = :vpago, valor_previsto = :vprev,
                    data_pagamento = :dpgto, data_vencimento = :dvenc, dac = :dac,
                    status = 'CONCILIADO', mensagem = NULL
                WHERE id = :id
            """), params_match
        )
    else:
        session.execute(
            text("""
                INSERT INTO payment_match (
                    retorno_id, remessa_id, student_id, student_responsible_id,
                    nome_responsavel, cpf_responsavel, lancamento_codigo, valor_pago,
                    valor_previsto, data_pagamento, data_vencimento, dac, status
                ) VALUES (
                    :rid, :remid, :sid, :srid, :nome_resp, :cpf_resp,
                    :lcod, :vpago, :vprev, :dpgto, :dvenc, :dac, 'CONCILIADO'
                )
            """), params_match
        )

    session.execute(text("UPDATE retorno SET status = 'CONCILIADO' WHERE id = :rid"), {"rid": ret.id})
    if rem:
        session.execute(
            text("UPDATE remessa SET status = 'PAGO' WHERE id = :remid"),
            {"remid": rem.id}
        )

    logger.info(f"Retorno {ret.id}: CONCILIADO (nosso número {ret.nosso_numero})")

    # Descrição do lançamento do Sophia (prioritária)
    descricao_lancamento = lanc.get("descricao")
    if descricao_lancamento:
        session.execute(
            text("UPDATE payment_match SET descricao_pagamento = :desc WHERE retorno_id = :rid"),
            {"desc": descricao_lancamento, "rid": ret.id}
        )
    else:
        descricao_remessa = obter_descricao_pagamento(session, ret.nosso_numero)
        if descricao_remessa:
            session.execute(
                text("UPDATE payment_match SET descricao_pagamento = :desc WHERE retorno_id = :rid"),
                {"desc": descricao_remessa, "rid": ret.id}
            )

    # Envio ao Conta Azul
    try:
        from app.services.conta_azul_receitas import criar_receita_com_baixa
        data_pgto_str = ret.data_pagamento.strftime('%Y-%m-%d') if isinstance(ret.data_pagamento, datetime) else str(ret.data_pagamento)[:10]
        descricao_final = lanc.get("descricao") or obter_descricao_pagamento(session, ret.nosso_numero) or 'Mensalidade Escolar'
        descricao_completa = f"{descricao_final} - Resp: {nome}"

        parcela_id = criar_receita_com_baixa(
            data_vencimento=data_pgto_str,
            valor=float(ret.valor_pago),
            descricao=descricao_completa,
            nome_cliente=nome,
            data_pagamento=data_pgto_str
        )

        session.execute(
            text("UPDATE payment_match SET conta_azul_receita_id = :caid WHERE retorno_id = :rid"),
            {"caid": parcela_id, "rid": ret.id}
        )
        logger.info(f"Receita Conta Azul criada (ID: {parcela_id})")
    except Exception as e:
        logger.error(f"Falha ao enviar para Conta Azul: {e}")
        session.execute(
            text("UPDATE payment_match SET mensagem = :msg WHERE retorno_id = :rid"),
            {"msg": f"Erro Conta Azul: {str(e)[:200]}", "rid": ret.id}
        )


def registrar_pendente_revisao(session: Session, ret, mensagem: str, nome_pagador: Optional[str] = None) -> None:
    """Registra pendência de revisão manual."""
    if nome_pagador and nome_pagador.strip():
        nome_limpo = normalizar_texto(nome_pagador)
        existe = session.execute(
            text("SELECT 1 FROM student_responsible WHERE UPPER(nome) = :nome AND responsavel_financeiro = true LIMIT 1"),
            {"nome": nome_limpo}
        ).first()
        if not existe:
            nome = f"{nome_limpo} (Não cadastrado no Sophia)"
        else:
            nome = nome_limpo
    else:
        nome = f"PAGADOR NÃO IDENTIFICADO – Nosso Número {ret.nosso_numero}"

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

    session.execute(text("UPDATE retorno SET status = 'PENDENTE_REVISAO' WHERE id = :rid"), {"rid": ret.id})


# ==============================================================================
# ORQUESTRAÇÃO PRINCIPAL
# ==============================================================================

def conciliar_retorno(api: SofiaAPI) -> None:
    """Orquestra a esteira de conciliação em lote."""
    session = SessionLocal()
    try:
        retornos = session.execute(
            text("SELECT * FROM retorno WHERE status IN ('PENDENTE', 'PENDENTE_REVISAO')")
        ).fetchall()

        if not retornos:
            logger.info("Nenhum retorno pendente para conciliar.")
            return

        logger.info(f"Iniciando conciliação de {len(retornos)} retornos...")

        for ret in retornos:
            rem = obter_remessa_por_nosso_numero(session, ret.nosso_numero, dac=getattr(ret, 'dac', None))

            if not rem:
                registrar_pendente_revisao(session, ret, f"Remessa não localizada para o Nosso Número {ret.nosso_numero}.")
                continue

            nome_busca = normalizar_texto(rem.nome_pagador)
            cpf_busca = rem.cpf_pagador

            if not nome_busca or nome_busca.isdigit():
                registrar_pendente_revisao(session, ret, "Nome do pagador inválido na remessa.", nome_pagador=None)
                continue

            resp_rows = buscar_responsaveis(session, nome=nome_busca, cpf=cpf_busca)
            if not resp_rows:
                registrar_pendente_revisao(session, ret, f"Responsável '{nome_busca}' não localizado no banco local.", nome_pagador=nome_busca)
                continue

            match_resp = None
            match_lanc = None
            for resp in resp_rows:
                lanc = encontrar_lancamento(
                    api, resp["student_sophia_id"], ret.nosso_numero,
                    float(ret.valor_pago), ret.data_pagamento
                )
                if lanc:
                    match_resp = resp
                    match_lanc = lanc
                    break

            if match_resp and match_lanc:
                registrar_conciliacao(session, ret, rem, match_resp, match_lanc)
            else:
                registrar_pendente_revisao(session, ret, "Lançamento financeiro não localizado no Sofia.", nome_pagador=nome_busca)

        session.commit()
        logger.info("Processo de conciliação finalizado com sucesso.")

    except Exception:
        session.rollback()
        logger.exception("Erro crítico durante a conciliação. Rollback executado.")
        raise
    finally:
        session.close()