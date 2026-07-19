# tests/test_reader.py
from utils.reader import RemessaReader
import pytest

def test_extrair_campo():
    reader = RemessaReader("dummy.rem")
    linha = "1" + " " * 125 + "0000000123890" + " " * 200 # Linha simulada
    # Ajuste o índice conforme seu layout
    valor = reader._extrair_campo(linha, 126, 139)
    assert valor == "0000000123890"

def test_processamento_registro_valido():
    reader = RemessaReader("dummy.rem")
    
    # Criamos uma string vazia longa o suficiente (ex: 300 caracteres)
    linha_lista = [" "] * 300
    
    # Preenchemos exatamente onde o leitor espera ler
    # Posição 0 (tipo registro)
    linha_lista[0] = '1'
    
    # Posições 120-126 (vencimento)
    data = "100726"
    for i, char in enumerate(data):
        linha_lista[120 + i] = char
        
    # Posições 126-139 (valor: 123890)
    valor = "0000000123890"
    for i, char in enumerate(valor):
        linha_lista[126 + i] = char
        
    # Posições 234-274 (nome)
    nome = "PAGADOR TESTE"
    for i, char in enumerate(nome):
        linha_lista[234 + i] = char
        
    linha = "".join(linha_lista)
    
    reader._processar_registro_tipo_1(linha, 1)
    
    assert len(reader.registros) == 1
    assert reader.registros[0]["nome_pagador"] == "PAGADOR TESTE"
    assert reader.registros[0]["valor"] == 1238.9
    assert reader.registros[0]["vencimento"] == "2026-07-10"