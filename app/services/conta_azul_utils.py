# services/conta_azul_utils.py
import logging
import requests
from sqlalchemy import text
from .conta_azul import _get_valid_access_token
from app.services.cache_sync import SessionLocal

logger = logging.getLogger(__name__)

def obter_configuracao():
    """Busca os UUIDs de conta e categoria do banco."""
    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT conta_financeira, categoria FROM conta_azul_config ORDER BY id LIMIT 1")
        ).first()
        if row:
            return {"conta_financeira": row.conta_financeira, "categoria": row.categoria}
        return None
    finally:
        session.close()

def definir_configuracao(conta_financeira: str, categoria: str):
    """Salva os UUIDs de conta e categoria (upsert na linha id=1)."""
    session = SessionLocal()
    try:
        session.execute(
            text("""
                INSERT INTO conta_azul_config (id, conta_financeira, categoria)
                VALUES (1, :conta, :cat)
                ON CONFLICT (id) DO UPDATE SET conta_financeira = EXCLUDED.conta_financeira,
                                              categoria = EXCLUDED.categoria,
                                              atualizado_em = NOW()
            """),
            {"conta": conta_financeira, "cat": categoria}
        )
        session.commit()
    finally:
        session.close()

def obter_ou_criar_contato(nome: str) -> str:
    """Retorna o UUID do contato (cliente) no Conta Azul.
    Se não existir no banco local, busca na API e cria se necessário.
    """
    nome = nome.strip().upper()
    session = SessionLocal()
    try:
        row = session.execute(
            text("SELECT contato_uuid FROM conta_azul_contato WHERE nome = :nome"),
            {"nome": nome}
        ).first()
        if row:
            return row.contato_uuid

        # Buscar na API
        token = _get_valid_access_token()
        resp = requests.get(
            "https://api-v2.contaazul.com/v1/pessoas",
            headers={"Authorization": f"Bearer {token}"},
            params={"nome": nome}
        )
        resp.raise_for_status()
        pessoas = resp.json()
        if pessoas:
            uuid = pessoas[0]["id"]
        else:
            # Criar novo contato
            payload = {"nome": nome, "tipo": "FISICA"}  # assumindo pessoa física
            resp = requests.post(
                "https://api-v2.contaazul.com/v1/pessoas",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=payload
            )
            resp.raise_for_status()
            uuid = resp.json()["id"]

        # Salvar mapeamento local
        session.execute(
            text("INSERT INTO conta_azul_contato (nome, contato_uuid) VALUES (:nome, :uuid) ON CONFLICT DO NOTHING"),
            {"nome": nome, "uuid": uuid}
        )
        session.commit()
        return uuid
    finally:
        session.close()

def obter_ou_criar_categoria(nome: str = "Recebimentos") -> str:
    token = _get_valid_access_token()
    # Buscar categoria pelo nome e tipo RECEITA
    resp = requests.get(
        "https://api-v2.contaazul.com/v1/categorias",
        headers={"Authorization": f"Bearer {token}"},
        params={"nome": nome, "tipo": "RECEITA", "tamanho_pagina": 1}
    )
    resp.raise_for_status()
    categorias = resp.json().get("itens", [])
    if categorias:
        return categorias[0]["id"]

    # Tentar criar a categoria
    try:
        resp = requests.post(
            "https://api-v2.contaazul.com/v1/categorias",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"nome": nome, "tipo": "RECEITA"}
        )
        resp.raise_for_status()
        return resp.json()["id"]
    except Exception as e:
        logger.error(f"Não foi possível criar a categoria: {e}")
        raise Exception(
            "Não existe categoria de receita 'Recebimentos' e não foi possível criá-la. "
            "Crie manualmente uma categoria no Conta Azul ou informe o UUID de uma existente."
        )

def listar_contas_financeiras():
    token = _get_valid_access_token()
    resp = requests.get(
        "https://api-v2.contaazul.com/v1/conta-financeira",
        headers={"Authorization": f"Bearer {token}"},
        params={"tamanho_pagina": 50, "apenas_ativo": True}
    )
    resp.raise_for_status()
    contas = resp.json().get("itens", [])
    return [{"id": c["id"], "nome": c["nome"]} for c in contas]