# services/conta_azul.py
import os
import base64
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple
from dotenv import load_dotenv, find_dotenv
import requests
import streamlit as st
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------------------
# Configurações (via secrets do Streamlit)
# ------------------------------------------------------------------------------

load_dotenv(find_dotenv())

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")  # None se não existir

TOKEN_URL = "https://auth.contaazul.com/oauth2/token"
AUTH_URL = "https://auth.contaazul.com/login"
API_BASE_URL = "https://api-v2.contaazul.com"

DATABASE_URL = os.getenv("DATABASE_URL")  # manter os.getenv para DATABASE_URL
engine = create_engine(DATABASE_URL)

# ------------------------------------------------------------------------------
# Funções de criptografia (se ENCRYPTION_KEY existir)
# ------------------------------------------------------------------------------

def _encrypt_token(token: str) -> str:
    """Criptografa o token usando pgcrypto no banco (se chave disponível)."""
    if not ENCRYPTION_KEY:
        return token
    with engine.begin() as conn:
        encrypted = conn.execute(
            text("SELECT pgp_sym_encrypt(:token, :key)"),
            {"token": token, "key": ENCRYPTION_KEY}
        ).scalar()
        return encrypted

def _decrypt_token(encrypted_token: str) -> str:
    """Descriptografa o token."""
    if not ENCRYPTION_KEY:
        return encrypted_token
    with engine.begin() as conn:
        decrypted = conn.execute(
            text("SELECT pgp_sym_decrypt(:encrypted, :key)"),
            {"encrypted": encrypted_token, "key": ENCRYPTION_KEY}
        ).scalar()
        return decrypted

# ------------------------------------------------------------------------------
# Gerenciamento de credenciais no banco
# ------------------------------------------------------------------------------

def _get_credentials():
    """Retorna as credenciais descriptografadas (id, access_token, refresh_token, expires_at)."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, access_token, refresh_token, expires_at FROM conta_azul_credentials ORDER BY id LIMIT 1")
        ).first()
    if row:
        return {
            "id": row.id,
            "access_token": _decrypt_token(row.access_token),
            "refresh_token": _decrypt_token(row.refresh_token),
            "expires_at": row.expires_at
        }
    return None

def _save_credentials(access_token: str, refresh_token: str, expires_in: int):
    """Salva/atualiza as credenciais criptografadas."""
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    enc_access = _encrypt_token(access_token)
    enc_refresh = _encrypt_token(refresh_token)
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO conta_azul_credentials (id, access_token, refresh_token, expires_at)
                VALUES (1, :access, :refresh, :exp)
                ON CONFLICT (id) DO UPDATE SET
                    access_token = EXCLUDED.access_token,
                    refresh_token = EXCLUDED.refresh_token,
                    expires_at = EXCLUDED.expires_at,
                    atualizado_em = NOW()
            """),
            {"access": enc_access, "refresh": enc_refresh, "exp": expires_at}
        )

def _get_valid_access_token() -> str:
    """Retorna um access_token válido, renovando se necessário."""
    creds = _get_credentials()
    if not creds or not creds["access_token"]:
        raise Exception("Token não encontrado. Autorize a aplicação.")
    if creds["expires_at"] and datetime.now(timezone.utc) + timedelta(minutes=1) >= creds["expires_at"]:
        logger.info("Token expirado, renovando...")
        return _refresh_token(creds["refresh_token"])
    return creds["access_token"]

def _refresh_token(current_refresh_token: str) -> str:
    """Renova o token e retorna o novo access_token."""
    basic_auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "refresh_token",
        "refresh_token": current_refresh_token
    }
    resp = requests.post(TOKEN_URL, headers=headers, data=data)
    resp.raise_for_status()
    tokens = resp.json()
    _save_credentials(tokens["access_token"], tokens["refresh_token"], tokens["expires_in"])
    return tokens["access_token"]

# ------------------------------------------------------------------------------
# Fluxo de autorização (para a interface)
# ------------------------------------------------------------------------------

def get_authorization_url() -> Tuple[str, str]:
    """Gera a URL de autorização e um state aleatório."""
    state = secrets.token_urlsafe(16)
    url = (
        f"{AUTH_URL}?"
        f"response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&state={state}"
        f"&scope=openid+profile+aws.cognito.signin.user.admin"
    )
    return url, state

def exchange_code(code: str, state: str = None):
    """Troca o código de autorização por tokens e salva no banco."""
    # Verificação de state será feita na interface
    basic_auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI
    }
    resp = requests.post(TOKEN_URL, headers=headers, data=data)
    resp.raise_for_status()
    tokens = resp.json()
    _save_credentials(tokens["access_token"], tokens["refresh_token"], tokens["expires_in"])
    logger.info("Tokens obtidos com sucesso.")

# ------------------------------------------------------------------------------
# Função de API (exemplo)
# ------------------------------------------------------------------------------

def atualizar_parcela(parcela_id: str, status: str = "QUITADO/RECEBIDO") -> dict:
    token = _get_valid_access_token()
    url = f"{API_BASE_URL}/v1/financeiro/eventos-financeiros/parcelas/{parcela_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    payload = {"status": status}
    resp = requests.patch(url, json=payload, headers=headers)
    resp.raise_for_status()
    return resp.json()

def get_credentials():
    """Retorna as credenciais descriptografadas (público)."""
    return _get_credentials()