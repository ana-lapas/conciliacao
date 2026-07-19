from utils.reader import RemessaReader

# Coloque o caminho do seu arquivo .rem aqui
caminho = "documentos_recebidos/Remessa25667 (1).rem" 

# Instancia a classe
leitor = RemessaReader(caminho)

# Executa o processamento
dados = leitor.processar()

# Exibe o resultado
print(f"Total de registros lidos: {len(dados)}")
for reg in dados[:5]: # Exibe os primeiros 5 para conferência
    print(reg)