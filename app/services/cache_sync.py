# services/cache_sync.py
import logging
import sys
import os
from datetime import datetime, timezone
from dotenv import load_dotenv, find_dotenv

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.services.sofia_api import SofiaAPI

load_dotenv(find_dotenv())

logger = logging.getLogger("cache_sync")
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# Configurações do banco (DATABASE_URL deve estar no .env)
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    logger.critical("DATABASE_URL não definida.")
    sys.exit(1)

# Engine e sessão
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine)

# Sophia
BASE_URL = os.getenv("SOFIA_BASE_URL")
TENANT   = os.getenv("SOFIA_TENANT")
USUARIO  = os.getenv("SOFIA_USUARIO")
SENHA    = os.getenv("SOFIA_SENHA")

if not all([BASE_URL, TENANT, USUARIO, SENHA]):
    logger.critical("Variáveis Sophia não definidas.")
    sys.exit(1)

def sync_students() -> None:
    """
    Sincroniza todos os alunos e seus responsáveis financeiros da API Sophia
    para as tabelas locais (student, student_responsible).
    Utiliza paginação e transações por página.
    """
    api = SofiaAPI(BASE_URL, TENANT, USUARIO, SENHA)
    api.autenticar()
    logger.info("Autenticação OK. Iniciando sincronização...")

    session = SessionLocal()
    try:
        # Obter ou criar tenant
        tenant_id = ensure_tenant(session)
        
        pagina = 1
        total_alunos = 0
        while True:
            try:
                alunos = api.listar_alunos(pagina=pagina, tamanho=100)
            except Exception as e:
                logger.error(f"Erro na página {pagina}: {e}")
                break
            if not alunos:
                break

            for aluno in alunos:
                try:
                    aluno_validado = validate_student_data(aluno)
                    upsert_student(session, aluno_validado, tenant_id)
                    total_alunos += 1
                except ValueError as e:
                    logger.warning(f"Erro ao validar aluno {aluno.get('codigo')}: {e}")
            session.commit()
            logger.info(f"Página {pagina}: {len(alunos)} alunos processados.")
            pagina += 1

        logger.info(f"Sincronização concluída. Total de alunos: {total_alunos}")
    except Exception:
        session.rollback()
        logger.exception("Erro durante a sincronização.")
        raise
    finally:
        session.close()
        api.close()
        engine.dispose()

def ensure_tenant(session) -> int:
    """
    Retorna o ID do tenant atual. Se não existir, insere um novo registro
    com as configurações atuais (sem armazenar senha).
    """
    result = session.execute(
        text("SELECT id FROM tenant WHERE name = :name"),
        {"name": TENANT}
    ).first()
    if result:
        return result[0]
    # Insere novo tenant (sem senha)
    result = session.execute(
        text("INSERT INTO tenant (name, sophia_base_url, sophia_username) VALUES (:name, :url, :user) ON CONFLICT (name) DO UPDATE SET name=EXCLUDED.name RETURNING id"),
        {"name": TENANT, "url": BASE_URL, "user": USUARIO}
    )
    return result.first()[0]

def validate_student_data(aluno: dict) -> dict:
    if not aluno.get("codigo"):
        raise ValueError("Aluno sem código")
    if not aluno.get("nome"):
        raise ValueError(f"Aluno {aluno.get('codigo')} sem nome")

    responsaveis = []
    for r in aluno.get("responsaveis", []):
        if r.get("responsavelFinanceiro"):
            responsaveis.append({
                "sophia_id": r.get("codigo"),
                "nome": (r.get("nome") or "").strip().upper(),
                "cpf": (r.get("cpf") or "").strip() or None,
                "email": (r.get("email") or "").strip() or None,
                "telefone": (r.get("telefone") or "").strip() or None,
            })

    return {
        "sophia_id": aluno["codigo"],
        "nome": (aluno.get("nome") or "").strip().upper(),
        "cpf": (aluno.get("cpf") or "").strip() or None,
        "email": (aluno.get("email") or "").strip() or None,
        "turma_principal": ((aluno.get("turmas") or [{}])[0].get("descricao") or "").strip() or None,
        "responsaveis_financeiros": responsaveis
    }

def upsert_student(session, validated: dict, tenant_id: int) -> None:
    """
    Insere ou atualiza o aluno e seus responsáveis financeiros.
    Recebe um dicionário já validado por validate_student_data.
    """
    sophia_id = validated["sophia_id"]
    nome = validated["nome"]
    cpf = validated["cpf"]
    email = validated["email"]
    turma = validated["turma_principal"]

    # Upsert do aluno
    session.execute(
        text("""
            INSERT INTO student (sophia_id, tenant_id, nome, cpf, email, turma_principal)
            VALUES (:sid, :tid, :nome, :cpf, :email, :turma)
            ON CONFLICT (sophia_id, tenant_id) DO UPDATE SET
                nome = EXCLUDED.nome,
                cpf = EXCLUDED.cpf,
                email = EXCLUDED.email,
                turma_principal = EXCLUDED.turma_principal,
                sincronizado_em = NOW()
        """),
        {"sid": sophia_id, "tid": tenant_id, "nome": nome, "cpf": cpf, "email": email, "turma": turma}
    )

    # Recupera o ID local do aluno
    student_id = session.execute(
        text("SELECT id FROM student WHERE sophia_id = :sid AND tenant_id = :tid"),
        {"sid": sophia_id, "tid": tenant_id}
    ).first()[0]

    # Remove responsáveis antigos
    session.execute(
        text("DELETE FROM student_responsible WHERE student_id = :sid"),
        {"sid": student_id}
    )

    # Insere os responsáveis financeiros validados
    for resp in validated["responsaveis_financeiros"]:
        result = session.execute(
            text("""
                INSERT INTO student_responsible
                    (student_id, sophia_id, nome, cpf, email, telefone, responsavel_financeiro)
                VALUES (:student_id, :sophia_id, :nome, :cpf, :email, :telefone, :resp_fin)
                ON CONFLICT (student_id, sophia_id) DO UPDATE SET
                    nome = EXCLUDED.nome,
                    cpf = EXCLUDED.cpf,
                    email = EXCLUDED.email,
                    telefone = EXCLUDED.telefone,
                    sincronizado_em = NOW()
            """),
            {
                "student_id": student_id,
                "sophia_id": resp["sophia_id"],
                "nome": resp["nome"],
                "cpf": resp.get("cpf"),
                "email": resp.get("email"),
                "telefone": resp.get("telefone"),
                "resp_fin": True
            }
        )
        if result.rowcount == 0:  # quando ON CONFLICT DO UPDATE não insere nova linha
            logger.debug(f"Responsável {resp['sophia_id']} já existente – atualizado.")