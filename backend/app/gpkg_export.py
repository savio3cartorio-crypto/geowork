"""Base vetorial do serviço em GeoPackage: uma camada por categoria, em SIRGAS 2000 (EPSG:4674).

Usa pyogrio, que traz o GDAL no próprio pacote. A geometria chega em WKB direto do PostGIS (ST_AsBinary).
"""

import os
import tempfile

import numpy as np
import pyogrio.raw

CRS = "EPSG:4674"

# camada -> (tipo de geometria, colunas (nome, tipo)); os tipos são 'int', 'float' ou 'str'
ESQUEMAS = {
    "perimetro": ("Polygon", [("ordem", "int")]),
    "sede": ("Point", [("ordem", "int")]),
    "vegetacao_nativa": ("Polygon", [("ordem", "int")]),
    "area_consolidada": ("Polygon", [("ordem", "int")]),
    "pousio": ("Polygon", [("ordem", "int")]),
    "servidao": ("Polygon", [("ordem", "int")]),
    "app": ("Polygon", [("ordem", "int")]),
    "uso_restrito": ("Polygon", [("ordem", "int")]),
    "rl": ("Polygon", [("ordem", "int")]),
    "rl_averbada": ("Polygon", [("ordem", "int")]),
    "rl_aprovada": ("Polygon", [("ordem", "int")]),
    "rl_compensacao": ("Polygon", [("ordem", "int")]),
    "edificacao": ("Polygon", [("ordem", "int")]),
    "hidro": ("LineString", [("ordem", "int")]),
    "curvas": ("MultiLineString", [("ordem", "int"), ("cota", "float"), ("intervalo", "float")]),
    "vertices": (
        "Point",
        [
            ("ordem", "int"),
            ("codigo", "str"),
            ("tipo", "str"),
            ("altitude", "float"),
            ("sigma_x", "float"),
            ("sigma_y", "float"),
            ("sigma_z", "float"),
            ("metodo", "str"),
        ],
    ),
}


def _coluna(linhas: list[dict], nome: str, tipo: str) -> np.ndarray:
    valores = [linha.get(nome) for linha in linhas]
    if tipo == "int":
        return np.array(valores, dtype=np.int64)
    if tipo == "float":
        return np.array([np.nan if v is None else float(v) for v in valores], dtype=np.float64)
    return np.array([None if v is None else str(v) for v in valores], dtype=object)


def gerar(camadas: dict[str, list[dict]]) -> bytes:
    """camadas: nome da camada -> linhas. Cada linha tem 'wkb' (bytes) e os atributos do esquema."""
    with tempfile.TemporaryDirectory() as pasta:
        caminho = os.path.join(pasta, "base.gpkg")
        gravadas = 0
        for nome, linhas in camadas.items():
            if not linhas:
                continue
            tipo, colunas = ESQUEMAS[nome]
            pyogrio.raw.write(
                caminho,
                np.array([linha["wkb"] for linha in linhas], dtype=object),
                [_coluna(linhas, c, t) for c, t in colunas],
                [c for c, _ in colunas],
                layer=nome,
                driver="GPKG",
                geometry_type=tipo,
                crs=CRS,
                append=gravadas > 0,
            )
            gravadas += 1
        if not gravadas:
            raise ValueError("o serviço não tem feições para exportar")
        with open(caminho, "rb") as arquivo:
            return arquivo.read()
