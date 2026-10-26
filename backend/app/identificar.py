"""Dados das feições das camadas públicas no ponto clicado no mapa ("Identificar").

Cada camada ativa é consultada no ponto: o SICAR pelo WFS (caixa mínima em volta do ponto e teste de
ponto no polígono aqui, porque o filtro espacial do geoserver do CAR não responde), o PAMGIA/IBAMA e a ANM
pela consulta por ponto do ArcGIS e o IBGE pelo GetFeatureInfo do WMS.
Só os campos listados abaixo são lidos: nome e CPF/CNPJ de embargado, autuado, titular minerário e
responsável técnico do SIGEF ficam de fora.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import httpx

from .consultas_publicas import ANM, PAMGIA, SICAR_WFS, UFS_URL, _fmt, _get_json

MAX_FEICOES = 5  # por camada; imóveis do CAR sobrepostos aparecem todos até esse limite
IBGE_WMS = "https://geoservicos.ibge.gov.br/geoserver/ows"
ITENS_SICAR_TIPO = {"IRU": "Imóvel rural", "AST": "Assentamento", "PCT": "Povos e comunidades tradicionais"}
ITENS_SICAR_STATUS = {"AT": "Ativo", "PE": "Pendente", "SU": "Suspenso", "CA": "Cancelado"}


def _data(valor) -> str | None:
    """Data do ArcGIS (milissegundos) ou ISO (WFS) em dd/mm/aaaa."""
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)):
        return datetime.fromtimestamp(valor / 1000).strftime("%d/%m/%Y")
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00")).strftime("%d/%m/%Y")
    except ValueError:
        return str(valor)


def _ha(valor) -> str | None:
    return None if valor in (None, "") else f"{_fmt(float(valor), 4)} ha"


def _num(casas: int):
    return lambda v: None if v in (None, "") else _fmt(float(v), casas)


def _de(tabela: dict):
    return lambda v: None if v in (None, "") else f"{tabela.get(v, v)} ({v})" if v in tabela else str(v)


# id -> nome, tipo, url, campos [(campo, rótulo, formato ou None)], título da feição
CAMADAS = {
    "sicar": ("Imóveis do CAR (SICAR)", "sicar", None, [
        ("cod_imovel", "Código do CAR", None),
        ("tipo_imovel", "Tipo", _de(ITENS_SICAR_TIPO)),
        ("status_imovel", "Situação do cadastro", _de(ITENS_SICAR_STATUS)),
        ("condicao", "Condição", None),
        ("area", "Área declarada", _ha),
        ("m_fiscal", "Módulos fiscais", _num(4)),
        ("municipio", "Município", None),
        ("uf", "UF", None),
        ("dat_criacao", "Inscrito em", _data),
    ], lambda a: a.get("cod_imovel")),
    "sigef": ("SIGEF privado (certificações até 04/2022)", "arcgis", f"{PAMGIA}/lim_imovel_sigef_privado_a/MapServer/9", [
        ("nome_area", "Denominação", None),
        ("parcela_co", "Código da parcela", None),
        ("codigo_imo", "Código do imóvel (SNCR)", None),
        ("status", "Situação", None),
        ("situacao_i", "Situação do imóvel", None),
        ("data_submi", "Submetida em", _data),
        ("data_aprov", "Certificada em", _data),
        ("registro_m", "Matrícula", None),
        ("registro_d", "Data do registro", _data),
        ("municipio_", "Município (código)", None),
        ("uf_id", "UF (código)", None),
    ], lambda a: a.get("nome_area") or a.get("parcela_co")),
    "sigef_publico": ("SIGEF público (certificações até 04/2022)", "arcgis",
                      f"{PAMGIA}/lim_imovel_sigef_publico_a/MapServer/10", None, None),
    "assentamento": ("Assentamentos (INCRA)", "arcgis", f"{PAMGIA}/assentamentos_incra/MapServer/1", [
        ("nome_proje", "Projeto", None),
        ("cd_sipra", "Código SIPRA", None),
        ("fase", "Fase", None),
        ("forma_obte", "Forma de obtenção", None),
        ("data_de_cr", "Criado em", _data),
        ("area_hecta", "Área", _ha),
        ("capacidade", "Capacidade (famílias)", None),
        ("num_famili", "Famílias assentadas", None),
        ("municipio", "Município", None),
        ("uf", "UF", None),
    ], lambda a: a.get("nome_proje")),
    "terra_indigena": ("Terras indígenas (FUNAI)", "arcgis", f"{PAMGIA}/lim_terra_indigena_a/MapServer/12", [
        ("terrai_nom", "Terra indígena", None),
        ("etnia_nome", "Etnia", None),
        ("fase_ti", "Fase", None),
        ("modalidade", "Modalidade", None),
        ("superficie", "Superfície", _ha),
        ("municipio_", "Município", None),
        ("uf_sigla", "UF", None),
        ("data_atual", "Atualizada em", _data),
    ], lambda a: a.get("terrai_nom")),
    "uc_federal": ("Unidades de conservação federais (ICMBio)", "arcgis",
                   f"{PAMGIA}/lim_unidade_conservacao_a/MapServer/13", [
        ("nome", "Nome", None),
        ("siglagrupo", "Grupo", None),
        ("anocriacao", "Ano de criação", None),
        ("atolegal", "Ato legal", None),
        ("areaha", "Área", _ha),
        ("administra", "Administração", None),
        ("municipios", "Municípios", None),
        ("uf", "UF", None),
    ], lambda a: a.get("nome")),
    "uc_estadual": ("Unidades de conservação estaduais", "arcgis",
                    f"{PAMGIA}/lim_unidade_conservacao_est_a/MapServer/14", [
        ("nome", "Nome", None),
        ("categoria", "Categoria", None),
        ("protecao", "Proteção", None),
        ("orgao", "Órgão", None),
        ("anocriacao", "Ano de criação", None),
        ("atolegal", "Ato legal", None),
        ("area_ha", "Área", _ha),
        ("uf", "UF", None),
    ], lambda a: a.get("nome")),
    "floresta_publica": ("Florestas públicas (SFB)", "arcgis", f"{PAMGIA}/lim_floresta_publica_a/MapServer/8", [
        ("nome", "Nome", None),
        ("categoria", "Categoria", None),
        ("classe", "Classe", None),
        ("tipo", "Tipo", None),
        ("governo", "Esfera", None),
        ("estagio", "Estágio", None),
        ("protecao", "Proteção", None),
        ("area_ha", "Área", _ha),
        ("bioma", "Bioma", None),
        ("uf", "UF", None),
    ], lambda a: a.get("nome") or "Floresta pública"),
    "embargo_ibama": ("Áreas embargadas (IBAMA)", "arcgis", f"{PAMGIA}/adm_embargos_ibama_a/MapServer/0", [
        ("num_tad", "Termo de embargo", None),
        ("serie_tad", "Série", None),
        ("dat_embargo", "Embargado em", _data),
        ("sit_desmatamento", "Situação", None),
        ("qtd_area_embargada", "Área embargada", _ha),
        ("num_processo", "Processo", None),
        ("municipio", "Município", None),
        ("uf", "UF", None),
    ], lambda a: f"Termo {a.get('num_tad')}{a.get('serie_tad') or ''}"),
    "embargo_icmbio": ("Embargos em UCs (ICMBio, 2022)", "arcgis", f"{PAMGIA}/adm_embargo_icmbio_a/MapServer/0", [
        ("numero_ai", "Auto de infração", None),
        ("serie", "Série", None),
        ("data_auto", "Lavrado em", _data),
        ("tipo_infra", "Tipo de infração", None),
        ("nome_uc", "Unidade de conservação", None),
        ("julgamento", "Julgamento", None),
        ("municipio", "Município", None),
        ("uf", "UF", None),
    ], lambda a: f"Auto {a.get('numero_ai')}{a.get('serie') or ''}"),
    "mineracao": ("Processos minerários ativos (ANM)", "arcgis", f"{ANM}/0", [
        ("PROCESSO", "Processo", None),
        ("FASE", "Fase", None),
        ("SUBS", "Substância", None),
        ("USO", "Uso", None),
        ("AREA_HA", "Área", _ha),
        ("ULT_EVENTO", "Último evento", None),
        ("UF", "UF", None),
    ], lambda a: f"Processo {a.get('PROCESSO')}"),
    "municipios": ("Municípios (IBGE)", "wms", "CCAR:BC250_2025_lml_municipio_a", [
        ("nome", "Município", None),
        ("geocodigo", "Código IBGE", None),
    ], lambda a: a.get("nome")),
    "hidrografia": ("Hidrografia (IBGE)", "wms", "CCAR:BC250_2025_hid", [
        ("nome", "Nome", None),
        ("tipotrechodrenagem", "Tipo", None),
        ("regime", "Regime", None),
        ("navegavel", "Navegável", None),
    ], lambda a: a.get("nome") or "Curso d'água sem nome"),
    "massa_dagua": ("Massas d'água (IBGE)", "wms", "CCAR:BC250_2025_hid_massa_dagua_a", [
        ("nome", "Nome", None),
        ("tipomassadagua", "Tipo", None),
        ("regime", "Regime", None),
    ], lambda a: a.get("nome") or "Massa d'água sem nome"),
}
# o SIGEF público tem os mesmos campos do privado
CAMADAS["sigef_publico"] = (*CAMADAS["sigef_publico"][:3], *CAMADAS["sigef"][3:])


# ---------- ponto no polígono (GeoJSON, em graus) ----------

def _no_anel(anel: list, x: float, y: float) -> bool:
    dentro = False
    for (x1, y1), (x2, y2) in zip(anel, anel[1:] + anel[:1]):
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            dentro = not dentro
    return dentro


def _contem(geometria: dict | None, x: float, y: float) -> bool:
    if not geometria:
        return False
    if geometria["type"] == "Polygon":
        poligonos = [geometria["coordinates"]]
    elif geometria["type"] == "MultiPolygon":
        poligonos = geometria["coordinates"]
    else:
        return False
    for aneis in poligonos:
        coords = [[(p[0], p[1]) for p in anel] for anel in aneis]
        if coords and _no_anel(coords[0], x, y) and not any(_no_anel(furo, x, y) for furo in coords[1:]):
            return True
    return False


# ---------- consulta de cada tipo de fonte (substituível nos testes) ----------

def _arcgis_ponto(url: str, campos: str, lng: float, lat: float) -> list[dict]:
    dados = _get_json(f"{url}/query", {
        "where": "1=1", "geometry": f"{lng},{lat}", "geometryType": "esriGeometryPoint", "inSR": 4674,
        "spatialRel": "esriSpatialRelIntersects", "outFields": campos, "outSR": 4674, "f": "geojson",
        "resultRecordCount": MAX_FEICOES,
    })
    return dados.get("features") or []


def _sicar_ponto(lng: float, lat: float) -> list[dict]:
    ufs = {f["properties"]["sigla"] for f in _arcgis_ponto(UFS_URL, "sigla", lng, lat)}
    feicoes = []
    for uf in sorted(ufs):
        d = 0.00001
        dados = _get_json(SICAR_WFS, {
            "service": "WFS", "version": "2.0.0", "request": "GetFeature",
            "typeNames": f"sicar:sicar_imoveis_{uf.lower()}", "outputFormat": "application/json",
            "count": 50, "bbox": f"{lng - d},{lat - d},{lng + d},{lat + d},EPSG:4674",
        })
        feicoes += [f for f in dados.get("features") or [] if _contem(f.get("geometry"), lng, lat)]
    return feicoes


def _wms_ponto(camada: str, lng: float, lat: float, grau_px: float) -> list[dict]:
    meio = 50 * grau_px  # janela de 101 x 101 pixels centrada no clique, na escala da tela
    dados = _get_json(IBGE_WMS, {
        "service": "WMS", "version": "1.1.1", "request": "GetFeatureInfo", "layers": camada,
        "query_layers": camada, "styles": "", "srs": "EPSG:4674",
        "bbox": f"{lng - meio},{lat - meio},{lng + meio},{lat + meio}", "width": 101, "height": 101,
        "x": 50, "y": 50, "info_format": "application/json", "feature_count": MAX_FEICOES,
    })
    return dados.get("features") or []


def buscar(camada_id: str, lng: float, lat: float, grau_px: float) -> list[dict]:
    _nome, tipo, url, campos, _titulo = CAMADAS[camada_id]
    if tipo == "sicar":
        return _sicar_ponto(lng, lat)
    if tipo == "wms":
        return _wms_ponto(url, lng, lat, grau_px)
    return _arcgis_ponto(url, ",".join(c for c, *_ in campos), lng, lat)


# ---------- montagem da resposta ----------

def _descrever(camada_id: str, feicao: dict) -> dict:
    _nome, tipo, _url, campos, titulo = CAMADAS[camada_id]
    attrs = feicao.get("properties") or {}
    linhas = []
    for campo, rotulo, formato in campos:
        valor = attrs.get(campo)
        texto = formato(valor) if formato else (None if valor in (None, "") else str(valor))
        if texto not in (None, ""):
            linhas.append([rotulo, texto])
    return {
        "titulo": str(titulo(attrs) or "Feição"),
        "campos": linhas,
        # o polígono do município do IBGE é grande demais para destacar; as outras feições vão junto
        "geometry": None if tipo == "wms" else feicao.get("geometry"),
    }


def identificar(camadas: list[str], lng: float, lat: float, grau_px: float, buscar_camada=None) -> dict:
    desconhecidas = [c for c in camadas if c not in CAMADAS]
    if desconhecidas:
        raise ValueError(f"camada desconhecida: {', '.join(desconhecidas)}")
    if not camadas:
        raise ValueError("ative ao menos uma camada pública para identificar")
    if not (-75 <= lng <= -28 and -35 <= lat <= 6):
        raise ValueError("ponto fora do Brasil")
    if not (0 < grau_px < 1):
        raise ValueError("escala inválida")
    buscar_camada = buscar_camada or buscar

    def uma(camada_id: str) -> dict:
        nome = CAMADAS[camada_id][0]
        try:
            feicoes = buscar_camada(camada_id, lng, lat, grau_px)
        except Exception as erro:  # noqa: BLE001 - a falha de uma fonte vira aviso na resposta
            msg = "fonte indisponível no momento" if isinstance(erro, httpx.HTTPError) else str(erro)
            return {"id": camada_id, "nome": nome, "ok": False, "erro": msg, "feicoes": []}
        return {"id": camada_id, "nome": nome, "ok": True,
                "feicoes": [_descrever(camada_id, f) for f in feicoes[:MAX_FEICOES]]}

    with ThreadPoolExecutor(max_workers=len(camadas)) as pool:
        return {"lat": lat, "lng": lng, "camadas": list(pool.map(uma, camadas))}
