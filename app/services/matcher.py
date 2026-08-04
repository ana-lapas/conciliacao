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

def normalizar_nosso_numero(nn: str) -> str:
    """
    Remove zeros à esquerda e caracteres não numéricos para igualar com as APIs.
    """
    if not nn:
        return ""
    apenas_numeros = re.sub(r'\D', '', str(nn))
    return apenas_numeros.lstrip('0')


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

def buscar_responsaveis(session: Session, nome: Optional[str] = None, cpf: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Localiza os responsáveis financeiros na tabela local por CPF ou Nome.
    """
    if cpf:
        cpf_limpo = re.sub(r'\D', '', str(cpf))
        query = text("""
            SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
            FROM student_responsible sr
            WHERE REPLACE(REPLACE(REPLACE(sr.cpf, '.', ''), '-', ''), ' ', '') = :cpf
              AND sr.responsavel_financeiro = true
        """)
        rows = session.execute(query, {"cpf": cpf_limpo}).fetchall()
        if rows:
            return [dict(row._mapping) for row in rows]

    if nome:
        nome_limpo = normalizar_texto(nome)
        partes = nome_limpo.split()
        primeiro_nome = partes[0] if partes else nome_limpo

        query = text("""
            SELECT sr.student_id, sr.id AS student_responsible_id, sr.nome, sr.cpf
            FROM student_responsible sr
            WHERE (UPPER(sr.nome) LIKE :nome_full OR UPPER(sr.nome) LIKE :nome_parcial)
              AND sr.responsavel_financeiro = true
        """)
        params = {
            "nome_full": f"%{nome_limpo}%",
            "nome_parcial": f"%{primeiro_nome}%"
        }
        rows = session.execute(query, params).fetchall()
        return [dict(row._mapping) for row in rows]

    return []


def obter_remessa_por_nosso_numero(session: Session, nosso_numero: str, dac: Optional[str] = None):
    """
    Busca a Remessa original pelo Nosso Número normalizado e opcionalmente pelo DAC.
    """
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

    # Fallback buscando apenas pelo nosso número caso o DAC venha vazio
    query_fallback = text("""
        SELECT * FROM remessa 
        WHERE LTRIM(nosso_numero, '0') = :nn_limpo 
           OR nosso_numero = :nn_raw
        LIMIT 1
    """)
    return session.execute(query_fallback, {"nn_limpo": nn_limpo, "nn_raw": nosso_numero}).first()

def obter_descricao_pagamento(session: Session, nosso_numero: str) -> Optional[str]:
    """
    Recupera as mensagens do Registro Tipo 2 vinculadas para compor o histórico.
    """
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
# LÓGICA DE MATCHING FINANCEIRO (CHAVE PRINCIPAL + VALOR)
# ==============================================================================

def encontrar_lancamento(
    api: SofiaAPI, student_id: int, nosso_numero: str,
    valor_pago: float, data_pagamento: Any
) -> Optional[Dict[str, Any]]:
    """
    Valida o lançamento em aberto cruzando o Nosso Número e tolerância de valor/vencimento.
    """
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
        if lanc.get("recebido") != 0:
            continue

        # 1. Match Direto pelo Nosso Número
        num_boleto_api = normalizar_nosso_numero(str(lanc.get("numeroBoleto", "")))
        if num_boleto_api and num_boleto_api == nn_limpo:
            return lanc

        # 2. Validação por Valor (tolerância de R$ 0,01 para juros/descontos)
        try:
            valor_previsto = float(lanc.get("valorPrevisto", 0))
        except (TypeError, ValueError):
            continue

        if abs(valor_previsto - valor_pago) > 0.01:
            continue

        # 3. Validação por Proximidade de Vencimento (janela de 5 dias)
        try:
            data_venc = datetime.strptime(str(lanc.get("dataVencimento", ""))[:10], '%Y-%m-%d')
        except (ValueError, TypeError):
            continue

        diferenca = abs((data_venc - data_pgto).days)
        if diferenca <= 5 and diferenca < melhor_diferenca:
            melhor_diferenca = diferenca
            melhor_lancamento = lanc

    return melhor_lancamento


# ==============================================================================
# PERSISTÊNCIA E INTEGRAÇÃO EXTERNA
# ==============================================================================

def registrar_conciliacao(session: Session, ret, rem, resp: dict, lanc: dict) -> None:
    """
    Efetiva a conciliação localmente e dispara a baixa no Conta Azul com tratamento defensivo.
    """
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
        # Dentro do loop de retornos em conciliar_retorno:
        rem = obter_remessa_por_nosso_numero(session, ret.nosso_numero, dac=getattr(ret, 'dac', None))

    # Integração Conta Azul com Tratamento Defensivo de Erros
    descricao_remessa = obter_descricao_pagamento(session, ret.nosso_numero)
    try:
        data_pgto_str = ret.data_pagamento.strftime('%Y-%m-%d') if isinstance(ret.data_pagamento, datetime) else str(ret.data_pagamento)[:10]
        descricao_completa = f"{descricao_remessa or 'Mensalidade Escolar'} - Resp: {nome}"
        
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
        logger.info(f"Retorno {ret.id} CONCILIADO e enviado ao Conta Azul (ID Parcela: {parcela_id})")
        
    except Exception as e:
        logger.error(f"Falha na integração Conta Azul para o retorno {ret.id}: {e}")
        session.execute(
            text("UPDATE payment_match SET mensagem = :msg WHERE retorno_id = :rid"),
            {"msg": f"Erro Conta Azul: {str(e)[:200]}", "rid": ret.id}
        )


def registrar_pendente_revisao(session: Session, ret, mensagem: str, nome_pagador: Optional[str] = None) -> None:
    """
    Registra pendência de revisão manual em caso de falha de match.
    """
    nome = normalizar_texto(nome_pagador) if nome_pagador else f"PAGADOR NÃO IDENTIFICADO – Nosso Número {ret.nosso_numero}"

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
    """
    Orquestra a esteira de conciliação em lote para todos os retornos pendentes.
    """
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
            rem = obter_remessa_por_nosso_numero(session, ret.nosso_numero)

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
                    api, resp["student_id"], ret.nosso_numero,
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