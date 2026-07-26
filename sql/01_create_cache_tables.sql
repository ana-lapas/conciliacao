-- ======================================================================
-- Criação das tabelas de cache local para conciliação Sophia ↔ CNAB
-- ======================================================================

-- Configuração da instituição (tenant)
CREATE TABLE IF NOT EXISTS tenant (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    sophia_base_url TEXT NOT NULL,
    sophia_username TEXT NOT NULL,
    -- A senha NÃO será armazenada; usaremos variáveis de ambiente
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Alunos (cache)
CREATE TABLE IF NOT EXISTS student (
    id              SERIAL PRIMARY KEY,
    sophia_id       INTEGER NOT NULL,
    tenant_id       INTEGER NOT NULL REFERENCES tenant(id) ON DELETE CASCADE,
    nome            TEXT NOT NULL,
    cpf             TEXT,
    email           TEXT,
    turma_principal TEXT,
    sincronizado_em TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (sophia_id, tenant_id)
);

-- Responsáveis financeiros (cache)
CREATE TABLE IF NOT EXISTS student_responsible (
    id                      SERIAL PRIMARY KEY,
    student_id              INTEGER NOT NULL REFERENCES student(id) ON DELETE CASCADE,
    nome                    TEXT NOT NULL,
    cpf                     TEXT,
    email                   TEXT,
    telefone                TEXT,
    responsavel_financeiro  BOOLEAN DEFAULT FALSE,
    sincronizado_em         TIMESTAMPTZ DEFAULT NOW()
);

-- Índices para busca rápida por nome e CPF
CREATE INDEX IF NOT EXISTS idx_resp_nome ON student_responsible (nome);
CREATE INDEX IF NOT EXISTS idx_resp_cpf  ON student_responsible (cpf);

-- Log de processamento de arquivos CNAB
CREATE TABLE IF NOT EXISTS processing_log (
    id              SERIAL PRIMARY KEY,
    cnab_file_id    INTEGER,                     -- referência opcional ao arquivo .ret processado
    step            TEXT,                         -- etapa (ex: 'leitura_ret', 'match', 'conta_azul')
    level           TEXT CHECK (level IN ('INFO', 'WARNING', 'ERROR')),
    message         TEXT,
    details         JSONB,                       -- dados extras (ex: nome do pagador, valor)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_log_level ON processing_log (level);
CREATE INDEX IF NOT EXISTS idx_log_created ON processing_log (created_at);