# tests/test_retorno_sync.py
import os
from dotenv import load_dotenv, find_dotenv
from app.services.retorno_sync import sincronizar_retorno

load_dotenv(find_dotenv())

if __name__ == "__main__":
    caminho = "documentos_recebidos/CB230600.RET"   # ajuste o caminho
    sincronizar_retorno(caminho)