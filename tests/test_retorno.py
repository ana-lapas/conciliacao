import pytest
from unittest.mock import patch, mock_open
from utils.retorno_reader import RetornoReader # Ajuste o import se o arquivo estiver em outro lugar

# Cria uma linha falsa que simula o layout CNAB 400 do Bradesco
# Tamanho total 400 caracteres
def criar_linha_detalhe(nosso_numero, valor_centavos, data_ddmmaa, ocorrencia):
    # O CNAB 400 exige precisão absoluta nas posições
    # Vamos montar usando f-strings para garantir que cada campo caia no seu índice
    linha = "1"                                    # pos 1
    linha += " " * 69                             # pos 2-70
    linha += nosso_numero.ljust(12)               # pos 71-82
    linha += " " * 26                             # pos 83-108
    linha += ocorrencia                           # pos 109-110
    linha += data_ddmmaa                          # pos 111-116
    linha += " " * 136                            # pos 117-253
    linha += valor_centavos.zfill(13)             # pos 254-266
    linha += " " * 134                            # pos 267-400
    return linha

def test_processar_detalhe_valido():
    reader = RetornoReader("dummy.ret")
    # Nosso numero nas posições 71-82, Valor 254-266, Data 111-116, Ocorrencia 109-110
    linha = criar_linha_detalhe("123456789012", "0000000010050", "180726", "06")
    
    reader._processar_detalhe(linha, 1)
    
    assert len(reader.registros) == 1
    assert reader.registros[0]["nosso_numero"] == "123456789012"
    assert reader.registros[0]["valor"] == 100.50
    assert reader.registros[0]["data"] == "2026-07-18"
    assert reader.registros[0]["ocorrencia"] == "06"

def test_processar_linha_invalida():
    reader = RetornoReader("dummy.ret")
    # Linha muito curta para disparar o IndexError
    linha = "1"
    
    reader._processar_detalhe(linha, 1)
    
    assert len(reader.registros) == 0 # Não deve adicionar registro

@patch("builtins.open", new_callable=mock_open, read_data="0HEADER\n1" + " "*399 + "\n9TRAILER")
def test_processar_arquivo_completo(mock_file):
    reader = RetornoReader("arquivo.ret")
    reader.processar()
    # O mock_file simula o arquivo. O processamento deve ler sem quebrar.
    assert True