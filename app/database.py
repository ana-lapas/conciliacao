-- Configurações da instituição (já que haverá apenas uma escola, mas mantendo escalabilidade)
CREATE TABLE tenant (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sophia_base_url TEXT NOT NULL,
    sophia_username TEXT NOT NULL,
    sophia_password TEXT NOT NULL,  -- armazenar criptografado (ex: pgcrypto)
    conta_azul_client_id TEXT,
    conta_azul_client_secret TEXT,
    last_student_sync TIMESTAMPTZ,  -- data da última sincronização de alunos
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Cache de alunos
CREATE TABLE student (
    id SERIAL PRIMARY KEY,
    sophia_id INTEGER NOT NULL,
    tenant_id INTEGER REFERENCES tenant(id),
    nome TEXT NOT NULL,
    cpf TEXT,
    email TEXT,
    turma_principal TEXT,
    sincronizado_em TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (sophia_id, tenant_id)
);

-- Cache de responsáveis financeiros
CREATE TABLE student_responsible (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES student(id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    cpf TEXT,
    email TEXT,
    telefone TEXT,
    responsavel_financeiro BOOLEAN DEFAULT FALSE,
    sincronizado_em TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_resp_nome ON student_responsible (nome);
CREATE INDEX idx_resp_cpf ON student_responsible (cpf);

-- Arquivos CNAB processados
CREATE TABLE cnab_file (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT DEFAULT 'pendente',  -- pendente, processando, concluído, erro
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pagamentos extraídos do arquivo de retorno
CREATE TABLE cnab_payment (
    id SERIAL PRIMARY KEY,
    file_id INTEGER REFERENCES cnab_file(id),
    nosso_numero TEXT,
    nome_sacado TEXT NOT NULL,
    cpf_sacado TEXT,
    data_pagamento DATE,
    valor_pago NUMERIC(12,2),
    data_credito DATE,
    raw_data JSONB
);

-- Parcelas baixadas (correspondências encontradas)
CREATE TABLE payment_match (
    id SERIAL PRIMARY KEY,
    cnab_payment_id INTEGER REFERENCES cnab_payment(id),
    student_id INTEGER REFERENCES student(id),
    lancamento_codigo INTEGER,          -- código do lançamento no Sophia
    valor_aplicado NUMERIC(12,2),
    data_vencimento DATE,
    status TEXT DEFAULT 'pendente',     -- pendente, quitado_conta_azul, erro
    conta_azul_parcela_id TEXT,
    mensagem_erro TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Log de processamento (para rastrear erros e ações)
CREATE TABLE processing_log (
    id SERIAL PRIMARY KEY,
    cnab_file_id INTEGER REFERENCES cnab_file(id),
    step TEXT,
    level TEXT CHECK (level IN ('INFO', 'WARNING', 'ERROR')),
    message TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);-- Configurações da instituição (já que haverá apenas uma escola, mas mantendo escalabilidade)
CREATE TABLE tenant (
    id SERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    sophia_base_url TEXT NOT NULL,
    sophia_username TEXT NOT NULL,
    sophia_password TEXT NOT NULL,  -- armazenar criptografado (ex: pgcrypto)
    conta_azul_client_id TEXT,
    conta_azul_client_secret TEXT,
    last_student_sync TIMESTAMPTZ,  -- data da última sincronização de alunos
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Cache de alunos
CREATE TABLE student (
    id SERIAL PRIMARY KEY,
    sophia_id INTEGER NOT NULL,
    tenant_id INTEGER REFERENCES tenant(id),
    nome TEXT NOT NULL,
    cpf TEXT,
    email TEXT,
    turma_principal TEXT,
    sincronizado_em TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (sophia_id, tenant_id)
);

-- Cache de responsáveis financeiros
CREATE TABLE student_responsible (
    id SERIAL PRIMARY KEY,
    student_id INTEGER REFERENCES student(id) ON DELETE CASCADE,
    nome TEXT NOT NULL,
    cpf TEXT,
    email TEXT,
    telefone TEXT,
    responsavel_financeiro BOOLEAN DEFAULT FALSE,
    sincronizado_em TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_resp_nome ON student_responsible (nome);
CREATE INDEX idx_resp_cpf ON student_responsible (cpf);

-- Arquivos CNAB processados
CREATE TABLE cnab_file (
    id SERIAL PRIMARY KEY,
    filename TEXT NOT NULL,
    status TEXT DEFAULT 'pendente',  -- pendente, processando, concluído, erro
    processed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Pagamentos extraídos do arquivo de retorno
CREATE TABLE cnab_payment (
    id SERIAL PRIMARY KEY,
    file_id INTEGER REFERENCES cnab_file(id),
    nosso_numero TEXT,
    nome_sacado TEXT NOT NULL,
    cpf_sacado TEXT,
    data_pagamento DATE,
    valor_pago NUMERIC(12,2),
    data_credito DATE,
    raw_data JSONB
);

-- Parcelas baixadas (correspondências encontradas)
CREATE TABLE payment_match (
    id SERIAL PRIMARY KEY,
    cnab_payment_id INTEGER REFERENCES cnab_payment(id),
    student_id INTEGER REFERENCES student(id),
    lancamento_codigo INTEGER,          -- código do lançamento no Sophia
    valor_aplicado NUMERIC(12,2),
    data_vencimento DATE,
    status TEXT DEFAULT 'pendente',     -- pendente, quitado_conta_azul, erro
    conta_azul_parcela_id TEXT,
    mensagem_erro TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Log de processamento (para rastrear erros e ações)
CREATE TABLE processing_log (
    id SERIAL PRIMARY KEY,
    cnab_file_id INTEGER REFERENCES cnab_file(id),
    step TEXT,
    level TEXT CHECK (level IN ('INFO', 'WARNING', 'ERROR')),
    message TEXT,
    details JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);