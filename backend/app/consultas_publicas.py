"""Consulta de sobreposição do imóvel com bases públicas oficiais.

São as mesmas bases de origem que o Mapa do Registro de Imóveis (ONR) reúne. Os dados do ONR ficam num
ArcGIS próprio, com token de sessão e sem API pública, por isso o GeoWork consulta as fontes originais:
- SICAR (geoserver do CAR, WFS): imóveis do CAR, com módulos fiscais;
- PAMGIA/IBAMA (ArcGIS, dados abertos): SIGEF, terras indígenas, UCs, assentamentos, florestas públicas
  e embargos IBAMA/ICMBio;
- ANM (SIGMINE): processos minerários ativos.

Cada fonte é consultada pela caixa envolvente do perímetro. As interseções são medidas no PostGIS.
Dados pessoais das bases (nome e CPF/CNPJ de embargado, titular) não são lidos nem devolvidos.
"""

import hashlib
import json
import re
import statistics
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import httpx
from psycopg import Connection

PAMGIA = "https://pamgia.ibama.gov.br/server/rest/services/01_Publicacoes_Bases"
SICAR_WFS = "https://geoserver.car.gov.br/geoserver/sicar/wfs"
SICAR_WMS = "https://geoserver.car.gov.br/geoserver/sicar/wms"
ANM = "https://geo.anm.gov.br/arcgis/rest/services/SIGMINE/dados_anm/MapServer"
TEMPO_LIMITE_S = 25
LIMITE_FEICOES = 1000
MARGEM_GRAUS = 0.02  # ~2 km em volta do perímetro: CARs vizinhos para estimar o módulo fiscal do município

# Faixa estreita ao longo da divisa é diferença de precisão entre as bases (o CAR é declarado sobre imagem),
# não sobreposição: largura média da interseção (área / meio perímetro) abaixo disso
LARGURA_LASCA_M = 3.0

# id, nome, nível de uma sobreposição, URL da camada ArcGIS, campos lidos, como descrever a feição, nota da base
FONTES_ARCGIS = [
    ("sigef", "SIGEF privado (INCRA)", "aviso", f"{PAMGIA}/lim_imovel_sigef_privado_a/MapServer/9",
     "parcela_co,codigo_imo,status,nome_area,registro_m",
     lambda a: f"parcela {a.get('nome_area') or a.get('parcela_co')} ({a.get('status') or 'sem situação'})",
     "cópia do IBAMA com certificações até 04/2022"),
    ("sigef_publico", "SIGEF público (INCRA)", "aviso", f"{PAMGIA}/lim_imovel_sigef_publico_a/MapServer/10",
     "parcela_co,codigo_imo,status,nome_area",
     lambda a: f"parcela {a.get('nome_area') or a.get('parcela_co')} ({a.get('status') or 'sem situação'})",
     "cópia do IBAMA com certificações até 04/2022"),
    ("terra_indigena", "Terra indígena (FUNAI)", "erro", f"{PAMGIA}/lim_terra_indigena_a/MapServer/12",
     "terrai_nom,fase_ti", lambda a: f"{a.get('terrai_nom')} ({a.get('fase_ti') or 'fase não informada'})", None),
    ("uc_federal", "Unidade de conservação federal (ICMBio)", "aviso", f"{PAMGIA}/lim_unidade_conservacao_a/MapServer/13",
     "nome,siglagrupo", lambda a: f"{a.get('nome')} ({a.get('siglagrupo') or 'grupo não informado'})", None),
    ("uc_estadual", "Unidade de conservação estadual", "aviso", f"{PAMGIA}/lim_unidade_conservacao_est_a/MapServer/14",
     "nome,categoria", lambda a: f"{a.get('nome')} ({a.get('categoria') or 'categoria não informada'})", None),
    ("assentamento", "Assentamento (INCRA)", "aviso", f"{PAMGIA}/assentamentos_incra/MapServer/1",
     "nome_proje,fase", lambda a: f"{a.get('nome_proje')} ({a.get('fase') or 'fase não informada'})", None),
    ("floresta_publica", "Floresta pública (SFB)", "aviso", f"{PAMGIA}/lim_floresta_publica_a/MapServer/8",
     "nome,categoria", lambda a: f"{a.get('nome') or 'sem nome'} ({a.get('categoria') or 'categoria não informada'})", None),
    ("embargo_ibama", "Embargo IBAMA", "erro", f"{PAMGIA}/adm_embargos_ibama_a/FeatureServer/0",
     "num_tad,serie_tad,dat_embargo,sit_desmatamento",
     lambda a: f"termo {a.get('num_tad')}{a.get('serie_tad') or ''}, de {_data_arcgis(a.get('dat_embargo'))}", None),
    ("embargo_icmbio", "Embargo ICMBio", "erro", f"{PAMGIA}/adm_embargo_icmbio_a/FeatureServer/0",
     "numero_ai,serie,data_auto,nome_uc",
     lambda a: f"auto {a.get('numero_ai')}{a.get('serie') or ''} ({a.get('nome_uc') or 'fora de UC'})", "base de 2022"),
    ("mineracao", "Processo minerário (ANM)", "info", f"{ANM}/0",
     "PROCESSO,FASE,SUBS", lambda a: f"processo {a.get('PROCESSO')}: {a.get('FASE')}, {a.get('SUBS')}", None),
]
NOMES = {"car": "CAR (SICAR)", **{f[0]: f[1] for f in FONTES_ARCGIS}}
UFS_URL = f"{PAMGIA}/lim_est_ibge_250k_2021/MapServer/7"


def _data_arcgis(ms) -> str:
    if not ms:
        return "data não informada"
    return datetime.fromtimestamp(ms / 1000).strftime("%d/%m/%Y")


def _fmt(valor: float, casas: int) -> str:
    return f"{valor:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------- acesso às fontes (substituível nos testes) ----------

def _get_json(url: str, params: dict) -> dict:
    with httpx.Client(timeout=TEMPO_LIMITE_S, headers={"User-Agent": "GeoWork/1.0"}) as cliente:
        r = cliente.get(url, params=params)
        r.raise_for_status()
        dados = r.json()
    if "error" in dados:
        raise RuntimeError(dados["error"].get("message") or "erro do servidor")
    return dados


def _arcgis(url: str, campos: str, caixa: tuple) -> list[dict]:
    dados = _get_json(f"{url}/query", {
        "where": "1=1", "geometry": ",".join(map(str, caixa)), "geometryType": "esriGeometryEnvelope",
        "inSR": 4674, "spatialRel": "esriSpatialRelIntersects", "outFields": campos, "outSR": 4674,
        "f": "geojson", "resultRecordCount": LIMITE_FEICOES,
    })
    return dados.get("features") or []


def _sicar(ufs: list[str], caixa: tuple) -> list[dict]:
    feicoes = []
    for uf in ufs:
        dados = _get_json(SICAR_WFS, {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"sicar:sicar_imoveis_{uf.lower()}", "outputFormat": "application/json",
            "count": LIMITE_FEICOES, "bbox": ",".join(map(str, caixa)) + ",EPSG:4674",
        })
        feicoes += dados.get("features") or []
    return feicoes


def buscar(caixa: tuple, caixa_ampla: tuple) -> dict[str, list[dict] | Exception]:
    """Busca cada fonte em paralelo. Fonte que falha devolve a exceção, sem derrubar as outras."""
    def seguro(funcao, *args):
        try:
            return funcao(*args)
        except Exception as erro:  # noqa: BLE001 - a falha de uma fonte vira aviso na resposta
            return erro

    def car():
        ufs = sorted({f["properties"]["sigla"] for f in _arcgis(UFS_URL, "sigla", caixa)})
        return _sicar(ufs, caixa_ampla)

    with ThreadPoolExecutor(max_workers=len(FONTES_ARCGIS) + 1) as pool:
        tarefas = {"car": pool.submit(seguro, car)}
        for fid, _nome, _nivel, url, campos, *_ in FONTES_ARCGIS:
            tarefas[fid] = pool.submit(seguro, _arcgis, url, campos, caixa)
        return {fid: t.result() for fid, t in tarefas.items()}


# ---------- análise ----------

def _medir(conn: Connection, servico_id: int, feicoes: list[dict]) -> list[dict]:
    """Área de cada feição dentro do perímetro (ha e % do imóvel), em geodésico."""
    if not feicoes:
        return []
    geometrias = json.dumps([json.dumps(f["geometry"]) for f in feicoes])
    linhas = conn.execute(
        """
        WITH p AS (SELECT geom FROM camadas_desenho WHERE servico_id = %s AND categoria = 'perimetro'),
        f AS (
          SELECT ordinality - 1 AS i, ST_MakeValid(ST_SetSRID(ST_GeomFromGeoJSON(g), 4674)) AS geom
          FROM json_array_elements_text(%s::json) WITH ORDINALITY AS t(g, ordinality)
        )
        SELECT i.i, ST_Area(i.geom::geography) / 10000 AS ha, ST_Perimeter(i.geom::geography) AS perimetro_m,
               ST_Area(p.geom::geography) / 10000 AS imovel_ha
        FROM (SELECT f.i, ST_Intersection(f.geom, p.geom) AS geom FROM f, p) i, p
        """,
        (servico_id, geometrias),
    ).fetchall()
    return sorted(linhas, key=lambda x: x["i"])


def _sugerir_mf(car: list[dict], municipio_do_imovel: str | None = None) -> dict | None:
    """Módulo fiscal do município a partir dos CARs vizinhos: área do imóvel / nº de módulos fiscais.
    Usa o município do CAR que mais cobre o perímetro; sem ele, o município com mais CARs na área."""
    por_municipio: dict[str, list[float]] = {}
    for f in car:
        a = f.get("properties") or {}
        if a.get("area") and a.get("m_fiscal") and a["m_fiscal"] > 0:
            por_municipio.setdefault(a.get("municipio") or "?", []).append(a["area"] / a["m_fiscal"])
    if not por_municipio:
        return None
    if municipio_do_imovel in por_municipio:
        municipio, valores = municipio_do_imovel, por_municipio[municipio_do_imovel]
    else:
        municipio, valores = max(por_municipio.items(), key=lambda kv: len(kv[1]))
    if len(valores) < 3:
        return None
    mediana = statistics.median(valores)
    concordam = sum(1 for v in valores if abs(v - mediana) <= 0.02 * mediana)
    if concordam < 0.8 * len(valores):
        return None  # valores espalhados: melhor não sugerir
    return {"municipio": municipio, "mf_ha": round(mediana, 1), "amostras": len(valores)}


def consultar(conn: Connection, servico: dict, buscar_fontes: Callable | None = None) -> dict:
    caixa_row = conn.execute(
        """
        SELECT ST_XMin(e) AS x0, ST_YMin(e) AS y0, ST_XMax(e) AS x1, ST_YMax(e) AS y1
        FROM (SELECT ST_Envelope(geom) AS e FROM camadas_desenho
              WHERE servico_id = %s AND categoria = 'perimetro') t
        """,
        (servico["id"],),
    ).fetchone()
    if caixa_row is None:
        raise ValueError("desenhe ou importe o perímetro antes de consultar as bases públicas")
    caixa = (caixa_row["x0"], caixa_row["y0"], caixa_row["x1"], caixa_row["y1"])
    caixa_ampla = (caixa[0] - MARGEM_GRAUS, caixa[1] - MARGEM_GRAUS, caixa[2] + MARGEM_GRAUS, caixa[3] + MARGEM_GRAUS)
    resultados = (buscar_fontes or buscar)(caixa, caixa_ampla)

    meu_car = (servico.get("codigo_car") or "").upper()
    meu_sncr = "".join(ch for ch in (servico.get("codigo_sncr") or "") if ch.isdigit())
    regras = {fid: (nivel, descrever, nota) for fid, _n, nivel, _u, _c, descrever, nota in FONTES_ARCGIS}
    regras["car"] = ("aviso", lambda a: f"{a.get('cod_imovel')} ({a.get('condicao') or a.get('status_imovel')})", None)

    fontes, achados = [], []
    car_feicoes: list[dict] = []
    municipio_do_imovel, maior_car_ha = None, 0.0
    toques = 0
    for fid, resultado in resultados.items():
        nivel, descrever, nota = regras[fid]
        if isinstance(resultado, Exception):
            fontes.append({"id": fid, "nome": NOMES[fid], "ok": False, "nota": nota,
                           "erro": "fonte indisponível no momento" if isinstance(resultado, httpx.HTTPError)
                           else str(resultado)})
            continue
        resultado = [f for f in resultado if f.get("geometry")]
        fontes.append({"id": fid, "nome": NOMES[fid], "ok": True, "nota": nota, "feicoes": len(resultado),
                       "parcial": len(resultado) >= LIMITE_FEICOES})
        if fid == "car":
            car_feicoes = resultado
        for feicao, medida in zip(resultado, _medir(conn, servico["id"], resultado)):
            ha = medida["ha"] or 0.0
            if ha <= 0:
                continue
            pct = 100 * ha / medida["imovel_ha"] if medida["imovel_ha"] else 0.0
            attrs = feicao.get("properties") or {}
            if fid == "car" and ha > maior_car_ha:
                municipio_do_imovel, maior_car_ha = attrs.get("municipio"), ha
            proprio = (fid == "car" and meu_car and (attrs.get("cod_imovel") or "").upper() == meu_car) or (
                fid in ("sigef", "sigef_publico") and meu_sncr and attrs.get("codigo_imo") == meu_sncr
            )
            texto = f"{NOMES[fid]}: {descrever(attrs)}, {_fmt(ha, 4)} ha ({_fmt(pct, 2)}% do imóvel)"
            if proprio:
                coberto = "cobre" if pct >= 95 else "cobre só"
                achados.append({"fonte": fid, "nivel": "ok" if pct >= 95 else "aviso", "ha": ha, "pct": pct,
                                "texto": f"{texto}. É o registro deste imóvel: {coberto} {_fmt(pct, 1)}% do perímetro."})
                continue
            largura_m = 2 * ha * 10000 / medida["perimetro_m"] if medida["perimetro_m"] else 0.0
            if largura_m < LARGURA_LASCA_M:
                toques += 1
                continue
            achados.append({"fonte": fid, "nivel": nivel, "ha": ha, "pct": pct, "texto": texto})

    ordem = {"erro": 0, "aviso": 1, "info": 2, "ok": 3}
    achados.sort(key=lambda a: (ordem[a["nivel"]], -a["ha"]))
    return {
        "consultado_em": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fontes": fontes,
        "achados": achados,
        "toques_de_divisa": toques,
        "mf_sugerido": _sugerir_mf(car_feicoes, municipio_do_imovel),
    }


# ---------- ladrilhos do SICAR repassados pelo servidor ----------
# O geoserver do CAR alterna respostas e quedas de conexão; com vários ladrilhos simultâneos o navegador
# recebe respostas que não são imagem (bloqueadas pelo ORB). O GeoWork pede, repete uma vez e guarda em cache.

LADRILHO_CACHE_DIAS = 7
_SEMAFORO_SICAR = threading.BoundedSemaphore(4)  # no máximo 4 pedidos simultâneos ao SICAR
PADRAO_CAMADAS_SICAR = re.compile(r"^sicar:sicar_imoveis_[a-z]{2}(,sicar:sicar_imoveis_[a-z]{2}){0,26}$")


def ladrilho_sicar(layers: str, bbox: str, tamanho: int, pasta_cache: Path) -> bytes:
    if not PADRAO_CAMADAS_SICAR.match(layers):
        raise ValueError("camadas do SICAR inválidas")
    try:
        caixa = [float(v) for v in bbox.split(",")]
    except ValueError:
        raise ValueError("bbox inválido") from None
    if len(caixa) != 4 or tamanho not in (256, 512):
        raise ValueError("bbox ou tamanho inválido")
    chave = hashlib.sha256(f"{layers}|{','.join(f'{v:.2f}' for v in caixa)}|{tamanho}".encode()).hexdigest()
    arquivo = pasta_cache / chave[:2] / f"{chave}.png"
    if arquivo.exists() and time.time() - arquivo.stat().st_mtime < LADRILHO_CACHE_DIAS * 86400:
        return arquivo.read_bytes()

    params = {
        "service": "WMS", "version": "1.1.1", "request": "GetMap", "layers": layers, "styles": "",
        "format": "image/png", "transparent": "true", "srs": "EPSG:3857",
        "bbox": ",".join(f"{v:.2f}" for v in caixa), "width": tamanho, "height": tamanho,
    }
    ultimo_erro: Exception | None = None
    with _SEMAFORO_SICAR:
        for _tentativa in range(2):
            try:
                with httpx.Client(timeout=20, headers={"User-Agent": "GeoWork/1.0"}) as cliente:
                    r = cliente.get(SICAR_WMS, params=params)
                if r.status_code == 200 and r.headers.get("content-type", "").startswith("image/png"):
                    arquivo.parent.mkdir(parents=True, exist_ok=True)
                    temporario = arquivo.with_suffix(".tmp")
                    temporario.write_bytes(r.content)
                    temporario.replace(arquivo)
                    return r.content
                ultimo_erro = RuntimeError(f"SICAR respondeu {r.status_code} {r.headers.get('content-type')}")
            except httpx.HTTPError as erro:
                ultimo_erro = erro
    raise ConnectionError(f"SICAR indisponível no momento ({ultimo_erro})")
