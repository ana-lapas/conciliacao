async def processar_arquivo_ret(tenant: Tenant, file_path: str):
    # 1. Autenticar no Sophia
    api = SofiaAPI(tenant.sophia_base_url, tenant.name,
                   tenant.sophia_username, tenant.sophia_password)
    api.autenticar()

    # 2. Sincronizar cache se necessário
    await sync_if_needed(tenant, api)

    # 3. Ler arquivo .ret
    reader = RetornoReader(file_path)
    reader.processar()
    pagamentos = reader.registros

    # 4. Para cada pagamento, buscar alunos via cache
    async with session.begin():
        for pag in pagamentos:
            # Busca responsáveis com nome similar (ILKIE)
            stmt = select(Student).join(StudentResponsible).where(
                StudentResponsible.nome.ilike(f"%{pag['nome_sacado']}%"),
                StudentResponsible.responsavel_financeiro == True
            )
            students = (await session.execute(stmt)).scalars().all()

            if not students:
                registrar_erro(pag, "Nenhum responsável financeiro encontrado com este nome.")
                continue

            # Para cada aluno, obtém lançamentos
            for student in students:
                lancamentos = api.obter_lancamentos(student.sophia_id)
                # Matcher (lógica de cruzamento)
                parcelas_a_quitar = matcher.cruzar(pag, lancamentos, student)
                # Gera payment_match e atualiza Conta Azul
                ...