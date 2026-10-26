"""Imagem de satélite do Google (híbrido: satélite com ruas e nomes) pela Map Tiles API oficial.

Só funciona com uma chave da Map Tiles API em GEOWORK_GOOGLE_MAPS_KEY. A chave vai na URL dos ladrilhos,
então precisa estar restrita, no Google Cloud, ao domínio do GeoWork e à Map Tiles API.
Os termos do Google exigem mostrar o logotipo e a atribuição da área visível (rota de atribuição abaixo).
"""

import threading
import time

import httpx

API = "https://tile.googleapis.com/v1"
_trava = threading.Lock()
_sessao: dict = {}


def sessao(chave: str) -> dict:
    """Sessão de ladrilhos (vale cerca de duas semanas); renovada um dia antes de vencer."""
    with _trava:
        if _sessao.get("chave") == chave and _sessao.get("expira", 0) - 86400 > time.time():
            return _sessao["dados"]
        r = httpx.post(
            f"{API}/createSession",
            params={"key": chave},
            json={"mapType": "satellite", "language": "pt-BR", "region": "BR", "layerTypes": ["layerRoadmap"]},
            timeout=20,
        )
        if r.status_code != 200:
            raise ConnectionError(f"Google recusou a sessão de mapa ({r.status_code})")
        corpo = r.json()
        dados = {"session": corpo["session"], "key": chave, "tileSize": corpo.get("tileWidth", 256)}
        _sessao.update(chave=chave, expira=int(corpo["expiry"]), dados=dados)
        return dados


def atribuicao(chave: str, zoom: int, norte: float, sul: float, leste: float, oeste: float) -> dict:
    dados = sessao(chave)
    r = httpx.get(
        f"{API}/viewport",
        params={"session": dados["session"], "key": chave, "zoom": zoom,
                "north": norte, "south": sul, "east": leste, "west": oeste},
        timeout=15,
    )
    if r.status_code != 200:
        raise ConnectionError(f"Google não devolveu a atribuição ({r.status_code})")
    corpo = r.json()
    return {"copyright": corpo.get("copyright", ""), "maxZoom": corpo.get("maxZoom")}
