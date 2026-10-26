"""Pacote para o SICAR: um shapefile por camada do CAR, em SIRGAS 2000 geográfico (EPSG:4674).

O Módulo de Cadastro do CAR importa shapefile, KML ou GPX camada por camada, na etapa Geo. Cada camada vai
num .zip próprio (.shp, .shx, .dbf, .prj e .cpg), e todos seguem juntos num .zip com um LEIA-ME que diz
em qual categoria do SICAR importar cada arquivo.
"""

import io
import os
import tempfile
import zipfile
from datetime import date

import numpy as np
import pyogrio.raw

CRS = "EPSG:4674"

# categoria do GeoWork -> (arquivo, categoria no SICAR, tipo de geometria), na ordem da etapa Geo.
# Edificações e curvas de nível não são camadas do CAR e ficam de fora.
CAMADAS = [
    ("perimetro", "01_area_do_imovel", "Área do imóvel", "Polygon"),
    ("sede", "02_sede", "Sede ou ponto de referência do imóvel", "Point"),
    ("vegetacao_nativa", "03_vegetacao_nativa", "Remanescente de vegetação nativa", "Polygon"),
    ("area_consolidada", "04_area_consolidada", "Área rural consolidada", "Polygon"),
    ("pousio", "05_pousio", "Área de pousio", "Polygon"),
    ("servidao", "06_servidao_administrativa", "Servidão administrativa", "Polygon"),
    ("app", "07_app", "Área de Preservação Permanente", "Polygon"),
    ("uso_restrito", "08_uso_restrito", "Área de uso restrito", "Polygon"),
    ("hidro", "09_curso_dagua", "Hidrografia (curso d'água em linha)", "LineString"),
    ("rl", "10_reserva_legal_proposta", "Reserva Legal proposta", "Polygon"),
    ("rl_averbada", "11_reserva_legal_averbada", "Reserva Legal averbada", "Polygon"),
    ("rl_aprovada", "12_reserva_legal_aprovada_nao_averbada", "Reserva Legal aprovada e não averbada", "Polygon"),
    ("rl_compensacao", "13_rl_compensacao_outro_imovel", "Reserva Legal vinculada à compensação de outro imóvel", "Polygon"),
]
CATEGORIAS = [c for c, *_ in CAMADAS]


def _fmt(valor: float, casas: int) -> str:
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _shapefile_zip(pasta: str, arquivo: str, tipo: str, linhas: list[dict]) -> bytes:
    """Grava a camada como shapefile e devolve o .zip com os arquivos que o SICAR lê."""
    caminho = os.path.join(pasta, f"{arquivo}.shp")
    campos = [np.array([linha["ordem"] for linha in linhas], dtype=np.int64)]
    nomes = ["ordem"]
    if tipo == "Polygon":
        campos.append(np.array([round(float(linha["area_ha"]), 4) for linha in linhas], dtype=np.float64))
        nomes.append("area_ha")
    elif tipo == "LineString":
        campos.append(np.array([round(float(linha["compr_m"]), 2) for linha in linhas], dtype=np.float64))
        nomes.append("compr_m")
    pyogrio.raw.write(
        caminho,
        np.array([linha["wkb"] for linha in linhas], dtype=object),
        campos,
        nomes,
        driver="ESRI Shapefile",
        geometry_type=tipo,
        crs=CRS,
        encoding="UTF-8",
    )
    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as z:
        for ext in ("shp", "shx", "dbf", "prj", "cpg"):
            parte = os.path.join(pasta, f"{arquivo}.{ext}")
            if os.path.exists(parte):
                z.write(parte, f"{arquivo}.{ext}")
    return saida.getvalue()


def gerar(servico: dict, camadas: dict[str, list[dict]]) -> bytes:
    """servico: nome_imovel, revisao, codigo_car. camadas: categoria -> linhas com wkb, ordem, area_ha,
    compr_m e invalida (motivo do ST_IsValidReason, ou None)."""
    if not camadas.get("perimetro"):
        raise ValueError("desenhe ou importe o perímetro antes de exportar para o SICAR")

    leia = [
        "PACOTE PARA O SICAR - GeoWork",
        f"Imóvel: {servico['nome_imovel']} (Rev {servico['revisao']:02d})",
    ]
    if servico.get("codigo_car"):
        leia.append(f"Código do CAR: {servico['codigo_car']}")
    leia += [
        f"Gerado em: {date.today().strftime('%d/%m/%Y')}",
        "Sistema de referência: SIRGAS 2000, coordenadas geográficas (EPSG:4674).",
        "",
        "Como importar: na etapa Geo do Módulo de Cadastro, escolha a categoria indicada abaixo e use",
        '"Importar um arquivo shapefile, kml ou gpx" com o .zip correspondente (sem descompactar).',
        "",
        "Arquivo -> categoria no SICAR (feições; área ou comprimento):",
    ]
    avisos: list[str] = []
    saida = io.BytesIO()
    with tempfile.TemporaryDirectory() as pasta, zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as pacote:
        for categoria, arquivo, nome_sicar, tipo in CAMADAS:
            linhas = camadas.get(categoria) or []
            if not linhas:
                continue
            pacote.writestr(f"{arquivo}.zip", _shapefile_zip(pasta, arquivo, tipo, linhas))
            if tipo == "Polygon":
                medida = f"{_fmt(sum(float(x['area_ha']) for x in linhas), 4)} ha"
            elif tipo == "LineString":
                medida = f"{_fmt(sum(float(x['compr_m']) for x in linhas), 2)} m"
            else:
                medida = "ponto"
            leia.append(f"  {arquivo}.zip -> {nome_sicar} ({len(linhas)}; {medida})")
            avisos += [
                f"  {nome_sicar}, feição {x['ordem']}: geometria inválida ({x['invalida']}). O SICAR pode recusar."
                for x in linhas
                if x.get("invalida")
            ]
        if not camadas.get("sede"):
            avisos.append("  Falta a sede ou ponto de referência, que é obrigatória no CAR.")
        leia += ["", "Áreas geodésicas (elipsoide), calculadas no PostGIS. O SICAR pode mostrar valores",
                 "ligeiramente diferentes, conforme a projeção que usa."]
        if avisos:
            leia += ["", "ATENÇÃO:", *avisos]
        pacote.writestr("LEIA-ME.txt", "\r\n".join(leia) + "\r\n")
    return saida.getvalue()
