# services/conta_azul_utils.py
import logging
import requests
from .conta_azul import _get_valid_access_token
from app.services.cache_sync import SessionLocal
import os
from sqlalchemy import create_engine, text

DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
logger = logging.getLogger(__name__)

def obter_configuracao():
    """
    Busca a configuração padrão salva no banco de dados local.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT conta_financeira, categoria FROM conta_azul_config LIMIT 1")
        ).first()
        
        if result:
            return {
                "conta_financeira_id": result.conta_financeira,
                "categoria_id": result.categoria
            }
        return None

def definir_configuracao(conta_financeira_id: str, categoria_id: str):
    """
    Salva ou atualiza a conta bancária e categoria de receita padrão na tabela do banco de dados.
    """
    with engine.begin() as conn:
        # Limpa registros antigos para manter apenas a configuração vigente
        conn.execute(text("DELETE FROM conta_azul_config;"))
        
        # Insere a nova configuração ativa usando os nomes de colunas existentes no banco
        conn.execute(
            text("""
                INSERT INTO conta_azul_config (conta_financeira, categoria)
                VALUES (:conta_id, :cat_id);
            """),
            {"conta_id": conta_financeira_id, "cat_id": categoria_id}
        )

import logging
import requests
from sqlalchemy import text
from .conta_azul import _get_valid_access_token

logger = logging.getLogger(__name__)

def obter_ou_criar_contato(nome: str, cpf: str = None) -> str:
    """Retorna o UUID do contato (cliente) no Conta Azul."""
    nome = nome.strip().upper()
    session = SessionLocal()
    try:
        # 1. Garante a criação da tabela local
        session.execute(
            text("""
                CREATE TABLE IF NOT EXISTS public.conta_azul_contato (
                    id SERIAL PRIMARY KEY,
                    nome VARCHAR(255) NOT NULL UNIQUE,
                    contato_uuid VARCHAR(255) NOT NULL,
                    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
        )
        session.commit()

        # 2. Busca no banco de dados local
        row = session.execute(
            text("SELECT contato_uuid FROM conta_azul_contato WHERE nome = :nome"),
            {"nome": nome}
        ).first()

        if row:
            return row.contato_uuid

        # 3. Busca na API do Conta Azul
        token = _get_valid_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

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
            # 4. Criar novo contato (apenas os campos estritamente necessários)
            payload = {
                "nome": nome,
                "tipo_pessoa": "Física",
                "perfis": [
                    {
                        "tipo_perfil": "Cliente"
                    }
                ]
            }

            if cpf and cpf.strip():
                payload["cpf"] = cpf.strip()

            resp_post = requests.post(
                "https://api-v2.contaazul.com/v1/pessoas",
                headers=headers,
                json=payload
            )
            resp_post.raise_for_status()
            uuid = resp_post.json()["id"]

        # 5. Salva o mapeamento no banco local
        session.execute(
            text("""
                INSERT INTO conta_azul_contato (nome, contato_uuid)
                VALUES (:nome, :uuid)
                ON CONFLICT (nome) DO NOTHING;
            """),
            {"nome": nome, "uuid": uuid}
        )
        session.commit()
        return uuid

    except requests.exceptions.HTTPError as http_err:
        session.rollback() # Limpa a transação que falhou
        
        status_code = http_err.response.status_code if http_err.response is not None else None
        body_text = http_err.response.text if http_err.response is not None else "Sem resposta"
        
        logger.error(f"Erro HTTP {status_code} na API Conta Azul ao criar/obter contato. Detalhes: {body_text}")
        
        # Persiste o erro de forma independente no banco
        registrar_log_erro(
            servico="obter_ou_criar_contato",
            status_code=status_code,
            resposta_erro=body_text,
            payload={"nome": nome, "cpf": cpf}
        )
        
        raise http_err

    except Exception as e:
        session.rollback()
        erro_msg = f"{type(e).__name__}: {str(e)}"
        logger.error(f"Erro inesperado ao obter/criar contato no Conta Azul: {erro_msg}")
        
        # Persiste exceções genéricas
        registrar_log_erro(
            servico="obter_ou_criar_contato",
            status_code=None,
            resposta_erro=erro_msg,
            payload={"nome": nome, "cpf": cpf}
        )
        
        raise e
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

def registrar_log_erro(servico: str, status_code: int | None, resposta_erro: str, payload: dict | str = None):
    """Grava o erro de integração em uma transação isolada no PostgreSQL."""
    session_log = SessionLocal()
    try:
        # Garante resiliência criando a tabela de log se não existir
        session_log.execute(
            text("""
                CREATE TABLE IF NOT EXISTS public.conta_azul_log (
                    id SERIAL PRIMARY KEY,
                    servico VARCHAR(100) NOT NULL,
                    status_code INT NULL,
                    payload_enviado TEXT NULL,
                    resposta_erro TEXT NULL,
                    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                );
            """)
        )
        
        session_log.execute(
            text("""
                INSERT INTO conta_azul_log (servico, status_code, payload_enviado, resposta_erro)
                VALUES (:servico, :status_code, :payload, :resposta);
            """),
            {
                "servico": servico,
                "status_code": status_code if isinstance(status_code, int) else None,
                "payload": str(payload) if payload else None,
                "resposta": resposta_erro
            }
        )
        session_log.commit()
    except Exception as log_err:
        session_log.rollback()
        logger.error(f"Falha ao persistir log de erro no DB: {log_err}")
    finally:
        session_log.close()

def traduzir_erro_para_usuario(exception: Exception) -> str:
    """Converte uma exceção técnica em uma mensagem clara para a secretária."""
    msg_original = str(exception)

    mapeamento = [
        ("403", "A conta do Conta Azul não está mais ativa. Entre em contato com o suporte do Conta Azul para reativar o plano."),
        ("END_TRIAL", "O período de testes do Conta Azul expirou. Por favor, assine um plano para continuar usando a integração."),
        ("401", "A conexão com o Conta Azul expirou. Clique em 'Conectar com Conta Azul' novamente para reautorizar."),
        ("Token não encontrado", "Você ainda não conectou ao Conta Azul. Vá até a aba 'Conta Azul' e clique em 'Conectar'."),
        ("date is not JSON serializable", "Erro interno: data inválida. Contate o suporte técnico."),
        ("Max retries exceeded", "Não foi possível conectar ao servidor. Verifique sua internet e tente novamente."),
        ("Failed to resolve", "Erro de rede. Verifique a conexão com a internet."),
    ]

    for chave, mensagem in mapeamento:
        if chave.lower() in msg_original.lower():
            return mensagem

    # Se não mapeou, retorna uma mensagem genérica (nunca mostre o erro técnico)
    return "Ocorreu um erro inesperado. Por favor, tente novamente ou contate o suporte."

def listar_categorias_receita() -> list:
    """Busca todas as categorias financeiras do tipo RECEITA na Conta Azul."""
    token = _get_valid_access_token()
    categorias = []
    
    # Lidando com a paginação (Fundamento de Escalabilidade)
    url = "https://api-v2.contaazul.com/v1/categorias"
    params = {"tipo": "RECEITA", "tamanho_pagina": 50, "pagina": 1}
    
    while True:
        resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params)
        resp.raise_for_status()
        
        dados = resp.json()
        itens = dados.get("itens", [])
        if not itens:
            break # Sai do loop quando não houver mais itens
            
        categorias.extend([{"id": c["id"], "nome": c["nome"]} for c in itens])
        params["pagina"] += 1 # Vai para a próxima página
        
    return categorias