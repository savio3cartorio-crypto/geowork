"""Consultas e operações PostGIS. Todas recebem uma conexão aberta pela camada de rotas.

Convenções:
- Coordenadas da API são [lng, lat] em SIRGAS 2000 geográfico (EPSG:4674).
- Operações métricas (buffer, offset) ocorrem na zona UTM SIRGAS 2000 do próprio dado.
- ValueError = entrada inválida (422); LookupError = não encontrado (404).
"""

import base64
import binascii
import json
import unicodedata
import uuid
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any

from psycopg import Connection

from .schemas import (
    ArquivoIn, ClienteIn, ConfrontanteIn, CurvasIn, PendenciaIn, PendenciaUpdate, ServicoIn, SituacaoCarIn,
    VerticeMeta,
)

COLUNAS_CLIENTE = (
    "id, nome, cpf_cnpj, email, telefone, cep, logradouro, numero, complemento, bairro, municipio, uf"
)

# Camadas de desenho no padrão do CAR (etapa Geo do SICAR), além de hidrografia e edificações
CATEGORIAS_FECHADAS = {  # polígonos
    "vegetacao_nativa", "area_consolidada", "pousio",
    "servidao", "app", "uso_restrito",
    "rl", "rl_averbada", "rl_aprovada", "rl_compensacao",
    "edificacao",
}
CATEGORIAS_LINEARES = {"hidro"}  # linhas
CATEGORIAS_PONTO = {"sede"}  # sede ou ponto de referência: um só por imóvel
CATEGORIAS_OFFSET = CATEGORIAS_FECHADAS | CATEGORIAS_LINEARES
CATEGORIAS_DESENHO = CATEGORIAS_OFFSET | CATEGORIAS_PONTO

# Parâmetros do ST_Buffer e do ST_OffsetCurve: cantos em esquadro (mitre), limite de 5x da espessura
OFFSET_OPTS = "join=mitre mitre_limit=5"

OFFSET_SQL = """
WITH src AS (
  SELECT ST_Transform(ST_SetSRID(ST_GeomFromGeoJSON(%(gj)s), 4674), %(srid)s) AS g
), res AS (
  SELECT ST_Transform(
    CASE WHEN ST_GeometryType(g) = 'ST_LineString'
         THEN ST_OffsetCurve(g, %(d)s, %(opts)s)
         ELSE ST_Buffer(g, %(d)s, %(opts)s)
    END, 4674) AS r
  FROM src
)
SELECT ST_AsGeoJSON(r) AS gj, ST_IsEmpty(r) AS vazio FROM res
"""


# ---------- utilidades geométricas ----------

def close_ring(points: list) -> list:
    return points if points[0] == points[-1] else [*points, points[0]]


def open_ring(points: list) -> list:
    return points[:-1] if points[0] == points[-1] else points


def utm_srid(lng: float) -> int:
    """SIRGAS 2000 / UTM: 18S = EPSG:31978 ... 25S = EPSG:31985 (cobre o Brasil)."""
    zone = int((lng + 180) // 6) + 1
    if not 18 <= zone <= 25:
        raise ValueError("coordenadas fora do território brasileiro (zonas UTM 18S a 25S)")
    return 31978 + zone - 18


def _iter_points(coords: Any) -> Iterator[list]:
    if coords and isinstance(coords[0], (int, float)):
        yield coords
        return
    for item in coords:
        yield from _iter_points(item)


def default_codigo(nome_imovel: str, index: int) -> str:
    """Padrão INCRA: ABCD-M-0001 (M = marco inicial, V = vértice)."""
    sem_acento = unicodedata.normalize("NFKD", nome_imovel).encode("ascii", "ignore").decode("ascii")
    letters = "".join(ch for ch in sem_acento.upper() if ch.isalnum())
    prefix = (letters + "XXXX")[:4]
    tipo = "M" if index == 0 else "V"
    return f"{prefix}-{tipo}-{index + 1:04d}"


def _polygon(ring: list) -> dict:
    ring = close_ring(ring)
    if len(ring) < 4:
        raise ValueError("polígono precisa de pelo menos 3 vértices distintos")
    return {"type": "Polygon", "coordinates": [ring]}


def _linestring(points: list) -> dict:
    if len(points) < 2:
        raise ValueError("linha precisa de pelo menos 2 pontos")
    return {"type": "LineString", "coordinates": points}


def _point(points: list) -> dict:
    if len(points) != 1:
        raise ValueError("ponto precisa de exatamente uma coordenada")
    return {"type": "Point", "coordinates": points[0]}


def _geometria(categoria: str, points: list) -> dict:
    if categoria in CATEGORIAS_FECHADAS:
        return _polygon(points)
    if categoria in CATEGORIAS_LINEARES:
        return _linestring(points)
    return _point(points)


def _insert_feature(conn: Connection, servico_id: int, categoria: str, ordem: int, geojson: dict) -> None:
    conn.execute(
        """
        INSERT INTO camadas_desenho (servico_id, categoria, ordem, geom)
        VALUES (%s, %s, %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4674))
        """,
        (servico_id, categoria, ordem, json.dumps(geojson)),
    )


def _require_servico(conn: Connection, servico_id: int) -> dict:
    row = conn.execute("SELECT * FROM servicos WHERE id = %s", (servico_id,)).fetchone()
    if row is None:
        raise LookupError(f"serviço {servico_id} não encontrado")
    return row


# ---------- empresa padrão ----------

def ensure_default_empresa(conn: Connection) -> int:
    """Protótipo single-tenant: usa a primeira empresa, criando uma se não houver."""
    row = conn.execute("SELECT id FROM empresas ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    return conn.execute("INSERT INTO empresas (razao_social) VALUES ('Empresa padrão') RETURNING id").fetchone()["id"]


# ---------- clientes ----------

def create_cliente(conn: Connection, data: ClienteIn) -> dict:
    empresa_id = ensure_default_empresa(conn)
    return conn.execute(
        f"""
        INSERT INTO clientes (empresa_id, nome, cpf_cnpj, email, telefone,
                              cep, logradouro, numero, complemento, bairro, municipio, uf)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING {COLUNAS_CLIENTE}
        """,
        (empresa_id, data.nome, data.cpf_cnpj, data.email, data.telefone, *_valores_endereco(data)),
    ).fetchone()


def _valores_endereco(data: ClienteIn) -> tuple:
    return (data.cep, data.logradouro, data.numero, data.complemento, data.bairro, data.municipio, data.uf)


def update_cliente(conn: Connection, cliente_id: int, data: ClienteIn) -> dict:
    if conn.execute("SELECT 1 FROM clientes WHERE id = %s", (cliente_id,)).fetchone() is None:
        raise LookupError(f"cliente {cliente_id} não encontrado")
    return conn.execute(
        f"""
        UPDATE clientes
        SET nome = %s, cpf_cnpj = %s, email = %s, telefone = %s,
            cep = %s, logradouro = %s, numero = %s, complemento = %s, bairro = %s, municipio = %s, uf = %s
        WHERE id = %s
        RETURNING {COLUNAS_CLIENTE}
        """,
        (data.nome, data.cpf_cnpj, data.email, data.telefone, *_valores_endereco(data), cliente_id),
    ).fetchone()


def list_clientes(conn: Connection) -> list[dict]:
    return conn.execute(f"SELECT {COLUNAS_CLIENTE} FROM clientes ORDER BY nome").fetchall()


# ---------- serviços ----------

def create_servico(conn: Connection, data: ServicoIn) -> dict:
    empresa_id = ensure_default_empresa(conn)
    cliente = conn.execute("SELECT id FROM clientes WHERE id = %s", (data.cliente_id,)).fetchone()
    if cliente is None:
        raise LookupError(f"cliente {data.cliente_id} não encontrado")
    return conn.execute(
        """
        INSERT INTO servicos (empresa_id, cliente_id, tipo, nome_imovel, codigo_car, codigo_sncr, matricula)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, cliente_id, tipo, nome_imovel, revisao, status
        """,
        (empresa_id, data.cliente_id, data.tipo, data.nome_imovel, data.codigo_car, data.codigo_sncr, data.matricula),
    ).fetchone()


def list_servicos(conn: Connection, cliente_id: int | None) -> list[dict]:
    if cliente_id is None:
        return conn.execute(
            "SELECT id, cliente_id, nome_imovel, revisao, status FROM servicos ORDER BY nome_imovel, revisao"
        ).fetchall()
    return conn.execute(
        """
        SELECT id, cliente_id, nome_imovel, revisao, status
        FROM servicos WHERE cliente_id = %s ORDER BY nome_imovel, revisao
        """,
        (cliente_id,),
    ).fetchall()


def _escapar_like(termo: str) -> str:
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def buscar_servicos(conn: Connection, termo: str, limite: int = 50) -> list[dict]:
    """Busca na base por nome do imóvel, código CAR, código INCRA/SNCR, matrícula, nome e documento do cliente."""
    padrao = f"%{_escapar_like(termo.strip())}%"
    return conn.execute(
        """
        SELECT s.id, s.cliente_id, s.nome_imovel, s.revisao, s.codigo_car, s.matricula,
               c.nome AS cliente_nome
        FROM servicos s
        LEFT JOIN clientes c ON c.id = s.cliente_id
        WHERE s.nome_imovel ILIKE %(p)s
           OR s.codigo_car ILIKE %(p)s
           OR s.codigo_sncr ILIKE %(p)s
           OR s.matricula ILIKE %(p)s
           OR c.nome ILIKE %(p)s
           OR c.cpf_cnpj ILIKE %(p)s
        ORDER BY s.nome_imovel, s.revisao
        LIMIT %(limite)s
        """,
        {"p": padrao, "limite": limite},
    ).fetchall()


def get_servico(conn: Connection, servico_id: int) -> dict:
    servico = _require_servico(conn, servico_id)
    camadas = conn.execute(
        """
        SELECT categoria, ordem, ST_AsGeoJSON(geom)::json AS geometry, propriedades
        FROM camadas_desenho WHERE servico_id = %s ORDER BY categoria, ordem
        """,
        (servico_id,),
    ).fetchall()
    vertices = conn.execute(
        """
        SELECT ordem AS indice, codigo, tipo, ST_X(geom) AS lng, ST_Y(geom) AS lat,
               altitude, sigma_x, sigma_y, sigma_z, metodo_posicionamento AS metodo
        FROM vertices_sigef WHERE servico_id = %s ORDER BY ordem
        """,
        (servico_id,),
    ).fetchall()
    confrontantes = conn.execute(
        """
        SELECT vi.ordem AS indice, c.tipo_divisa, c.nome, c.cpf_cnpj, c.matricula,
               c.cns_cartorio, c.codigo_incra_sncr, c.orgao_faixa
        FROM confrontantes_limites c
        JOIN vertices_sigef vi ON vi.id = c.vertice_inicio_id
        WHERE c.servico_id = %s ORDER BY vi.ordem
        """,
        (servico_id,),
    ).fetchall()
    return {**servico, "camadas": camadas, "vertices": vertices, "confrontantes": confrontantes}


def nova_revisao(conn: Connection, servico_id: int) -> dict:
    """Clona o serviço como próxima revisão, copiando camadas, vértices e confrontantes."""
    origem = _require_servico(conn, servico_id)
    rev = conn.execute(
        """
        SELECT COALESCE(MAX(revisao), -1) + 1 AS r FROM servicos
        WHERE empresa_id = %s AND cliente_id IS NOT DISTINCT FROM %s AND nome_imovel = %s
        """,
        (origem["empresa_id"], origem["cliente_id"], origem["nome_imovel"]),
    ).fetchone()["r"]

    novo = conn.execute(
        """
        INSERT INTO servicos (empresa_id, cliente_id, rt_id, tipo, nome_imovel, codigo_car, codigo_sncr,
                              matricula, modulo_fiscal_ha, area_documental_ha, area_ccir_ha,
                              area_car_declarada_ha, rl_percentual, car_situacao, car_situacao_data, car_prazo, revisao, servico_origem_id)
        SELECT empresa_id, cliente_id, rt_id, tipo, nome_imovel, codigo_car, codigo_sncr,
               matricula, modulo_fiscal_ha, area_documental_ha, area_ccir_ha,
               area_car_declarada_ha, rl_percentual, car_situacao, car_situacao_data, car_prazo, %(rev)s, id
        FROM servicos WHERE id = %(old)s
        RETURNING id, nome_imovel, revisao
        """,
        {"rev": rev, "old": servico_id},
    ).fetchone()
    params = {"new": novo["id"], "old": servico_id}

    conn.execute(
        """
        INSERT INTO camadas_desenho (servico_id, categoria, ordem, geom, propriedades)
        SELECT %(new)s, categoria, ordem, geom, propriedades FROM camadas_desenho WHERE servico_id = %(old)s
        """,
        params,
    )
    conn.execute(
        """
        INSERT INTO vertices_sigef (servico_id, codigo, ordem, tipo, geom, altitude, sigma_x, sigma_y,
                                    sigma_z, metodo_posicionamento)
        SELECT %(new)s, codigo, ordem, tipo, geom, altitude, sigma_x, sigma_y, sigma_z, metodo_posicionamento
        FROM vertices_sigef WHERE servico_id = %(old)s
        """,
        params,
    )
    # O CAR é do imóvel, não da revisão do desenho: histórico e pendências seguem para a revisão nova
    conn.execute(
        """
        INSERT INTO car_situacoes (servico_id, situacao, data, prazo, observacao, registrado_em)
        SELECT %(new)s, situacao, data, prazo, observacao, registrado_em FROM car_situacoes WHERE servico_id = %(old)s
        """,
        params,
    )
    conn.execute(
        """
        INSERT INTO car_pendencias (servico_id, descricao, criada_em, resolvida_em)
        SELECT %(new)s, descricao, criada_em, resolvida_em FROM car_pendencias WHERE servico_id = %(old)s
        """,
        params,
    )
    # Confrontantes apontam para vértices novos; o mapeamento é pelo índice (ordem) do vértice
    conn.execute(
        """
        INSERT INTO confrontantes_limites (servico_id, vertice_inicio_id, vertice_fim_id, tipo_divisa, nome,
                                           cpf_cnpj, matricula, cns_cartorio, codigo_incra_sncr, orgao_faixa)
        SELECT %(new)s, vi_new.id, vf_new.id, c.tipo_divisa, c.nome, c.cpf_cnpj, c.matricula,
               c.cns_cartorio, c.codigo_incra_sncr, c.orgao_faixa
        FROM confrontantes_limites c
        JOIN vertices_sigef vo ON vo.id = c.vertice_inicio_id
        JOIN vertices_sigef vf ON vf.id = c.vertice_fim_id
        JOIN vertices_sigef vi_new ON vi_new.servico_id = %(new)s AND vi_new.ordem = vo.ordem
        JOIN vertices_sigef vf_new ON vf_new.servico_id = %(new)s AND vf_new.ordem = vf.ordem
        WHERE c.servico_id = %(old)s
        """,
        params,
    )
    return get_servico(conn, novo["id"])


# ---------- camadas e perímetro ----------

def put_camada(conn: Connection, servico_id: int, categoria: str, features: list[list]) -> dict:
    _require_servico(conn, servico_id)
    if categoria not in CATEGORIAS_DESENHO:
        raise ValueError(f"categoria '{categoria}' não aceita aqui; o perímetro tem rota própria")
    if categoria in CATEGORIAS_PONTO and len(features) > 1:
        raise ValueError("o imóvel tem uma só sede ou ponto de referência")
    # Valida tudo antes de apagar: uma entrada ruim não deixa a camada vazia
    geometrias = [_geometria(categoria, points) for points in features]
    conn.execute(
        "DELETE FROM camadas_desenho WHERE servico_id = %s AND categoria = %s",
        (servico_id, categoria),
    )
    for ordem, geojson in enumerate(geometrias):
        _insert_feature(conn, servico_id, categoria, ordem, geojson)
    return {"categoria": categoria, "feicoes": len(geometrias)}


def put_camadas(conn: Connection, servico_id: int, camadas: dict[str, list[list]]) -> dict:
    """Substitui várias camadas de uma vez. Corre numa só transação: se uma falhar, nenhuma muda."""
    return {
        "camadas": {categoria: put_camada(conn, servico_id, categoria, features)["feicoes"]
                    for categoria, features in camadas.items()}
    }


def put_perimetro(
    conn: Connection,
    servico_id: int,
    ring: list,
    vertices: list[VerticeMeta],
    confrontantes: list[ConfrontanteIn],
) -> dict:
    servico = _require_servico(conn, servico_id)
    pontos = open_ring(ring)
    if len(pontos) < 3:
        raise ValueError("perímetro precisa de pelo menos 3 vértices")

    # Substitui o perímetro inteiro: vértices (e confrontantes, por cascata) são recriados
    conn.execute("DELETE FROM vertices_sigef WHERE servico_id = %s", (servico_id,))
    conn.execute(
        "DELETE FROM camadas_desenho WHERE servico_id = %s AND categoria = 'perimetro'",
        (servico_id,),
    )
    _insert_feature(conn, servico_id, "perimetro", 0, _polygon(pontos))

    ids = []
    for i, (lng, lat) in enumerate(pontos):
        meta = vertices[i] if i < len(vertices) else VerticeMeta()
        row = conn.execute(
            """
            INSERT INTO vertices_sigef (servico_id, codigo, ordem, tipo, geom, altitude,
                                        sigma_x, sigma_y, sigma_z, metodo_posicionamento)
            VALUES (%s, %s, %s, %s, ST_SetSRID(ST_MakePoint(%s, %s), 4674), %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                servico_id,
                meta.codigo or default_codigo(servico["nome_imovel"], i),
                i,
                "M" if i == 0 else "V",
                lng,
                lat,
                meta.altitude,
                meta.sigma_x,
                meta.sigma_y,
                meta.sigma_z,
                meta.metodo,
            ),
        )
        ids.append(row.fetchone()["id"])

    for conf in confrontantes:
        if conf.indice >= len(ids):
            raise ValueError(f"confrontante aponta para o vértice {conf.indice}, que não existe")
        conn.execute(
            """
            INSERT INTO confrontantes_limites (servico_id, vertice_inicio_id, vertice_fim_id, tipo_divisa, nome,
                                               cpf_cnpj, matricula, cns_cartorio, codigo_incra_sncr, orgao_faixa)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                servico_id,
                ids[conf.indice],
                ids[(conf.indice + 1) % len(ids)],
                conf.tipo_divisa,
                conf.nome,
                conf.cpf_cnpj,
                conf.matricula,
                conf.cns_cartorio,
                conf.codigo_incra_sncr,
                conf.orgao_faixa,
            ),
        )
    return {"vertices": len(ids), "confrontantes": len(confrontantes)}


# ---------- OFFSET ----------

def offset(conn: Connection, servico_id: int, categoria: str, indice: int, distancia_m: float) -> dict:
    """Cria a paralela de uma feição (ST_Buffer para polígonos, ST_OffsetCurve para linhas).

    A feição original é preservada, como no AutoCAD. A nova entra ao final da mesma categoria.
    """
    _require_servico(conn, servico_id)
    if categoria not in CATEGORIAS_OFFSET:
        raise ValueError("OFFSET só se aplica a rl, app, edificacao ou hidro; o perímetro não recebe paralela")
    if distancia_m == 0:
        raise ValueError("a distância do OFFSET não pode ser zero")

    origem = conn.execute(
        """
        SELECT ST_AsGeoJSON(geom)::json AS gj
        FROM camadas_desenho WHERE servico_id = %s AND categoria = %s AND ordem = %s
        """,
        (servico_id, categoria, indice),
    ).fetchone()
    if origem is None:
        raise LookupError(f"feição {categoria}[{indice}] não encontrada")

    gj = origem["gj"]
    pontos = list(_iter_points(gj["coordinates"]))
    lng_medio = sum(p[0] for p in pontos) / len(pontos)
    srid = utm_srid(lng_medio)

    resultado = conn.execute(
        OFFSET_SQL,
        {"gj": json.dumps(gj), "srid": srid, "d": distancia_m, "opts": OFFSET_OPTS},
    ).fetchone()
    if resultado["gj"] is None or resultado["vazio"]:
        raise ValueError("o OFFSET resultou em geometria vazia; reduza a distância")

    nova = json.loads(resultado["gj"])
    nova_ordem = conn.execute(
        "SELECT COALESCE(MAX(ordem), -1) + 1 AS n FROM camadas_desenho WHERE servico_id = %s AND categoria = %s",
        (servico_id, categoria),
    ).fetchone()["n"]
    _insert_feature(conn, servico_id, categoria, nova_ordem, nova)
    return {"categoria": categoria, "ordem": nova_ordem, "srid_utm": srid, "geometry": nova}


def update_servico(conn: Connection, servico_id: int, data) -> dict:
    _require_servico(conn, servico_id)
    conn.execute(
        """
        UPDATE servicos
        SET matricula = %s, codigo_car = %s, codigo_sncr = %s, modulo_fiscal_ha = %s,
            area_documental_ha = %s, area_ccir_ha = %s, area_car_declarada_ha = %s,
            rl_percentual = %s
        WHERE id = %s
        """,
        (
            data.matricula, data.codigo_car, data.codigo_sncr, data.modulo_fiscal_ha,
            data.area_documental_ha, data.area_ccir_ha, data.area_car_declarada_ha,
            data.rl_percentual, servico_id,
        ),
    )
    return get_servico(conn, servico_id)


def delete_perimetro(conn: Connection, servico_id: int) -> dict:
    """Remove o perímetro, seus vértices e confrontantes (por cascata)."""
    _require_servico(conn, servico_id)
    conn.execute("DELETE FROM vertices_sigef WHERE servico_id = %s", (servico_id,))
    conn.execute(
        "DELETE FROM camadas_desenho WHERE servico_id = %s AND categoria = 'perimetro'",
        (servico_id,),
    )
    return {"servico_id": servico_id, "perimetro": "removido"}


# ---------- arquivos do projeto (PDF, DXF, DOCX, XLSX, JPG, PNG), cada um numa pasta ----------

LIMITE_ARQUIVO_BYTES = 25 * 1024 * 1024
TIPOS_ARQUIVO = {".pdf": "PDF", ".dxf": "DXF", ".docx": "DOCX", ".xlsx": "XLSX",
                 ".jpg": "JPG", ".jpeg": "JPG", ".png": "PNG", ".csv": "CSV", ".txt": "TXT", ".kml": "KML"}


def _confere_conteudo(extensao: str, conteudo: bytes) -> None:
    """O conteúdo tem de bater com a extensão: não basta o nome do arquivo."""
    assinatura = {
        ".pdf": (b"%PDF",), ".png": (b"\x89PNG",), ".jpg": (b"\xff\xd8\xff",), ".jpeg": (b"\xff\xd8\xff",),
        ".docx": (b"PK\x03\x04",), ".xlsx": (b"PK\x03\x04",),
    }
    if extensao == ".dxf":
        ok = b"SECTION" in conteudo[:4096]
    elif extensao == ".kml":
        ok = b"<kml" in conteudo[:4096].lower()
    elif extensao in (".csv", ".txt"):
        try:
            conteudo.decode("utf-8")
            ok = b"\x00" not in conteudo
        except UnicodeDecodeError:
            ok = False
    else:
        ok = conteudo.startswith(assinatura[extensao])
    if not ok:
        raise ValueError(f"o conteúdo não é um arquivo {extensao[1:].upper()}")


def salvar_arquivo(conn: Connection, servico_id: int, data: ArquivoIn, storage_dir: Path) -> dict:
    _require_servico(conn, servico_id)
    extensao = Path(data.nome_arquivo).suffix.lower()
    if extensao not in TIPOS_ARQUIVO:
        raise ValueError("tipo de arquivo não aceito: use PDF, DXF, KML, DOCX, XLSX, CSV, TXT, JPG ou PNG")
    try:
        conteudo = base64.b64decode(data.conteudo_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("conteúdo do arquivo não está em base64 válido") from exc
    _confere_conteudo(extensao, conteudo)
    if len(conteudo) > LIMITE_ARQUIVO_BYTES:
        raise ValueError("arquivo maior que 25 MB")

    relativo = f"{servico_id}/{uuid.uuid4().hex}{extensao}"
    destino = storage_dir / relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)

    return conn.execute(
        """
        INSERT INTO arquivos_dados (servico_id, nome_arquivo, tipo, categoria, pasta, caminho)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, nome_arquivo, tipo, categoria, pasta, enviado_em
        """,
        (servico_id, data.nome_arquivo, TIPOS_ARQUIVO[extensao], data.categoria, data.pasta, relativo),
    ).fetchone()


def listar_arquivos(conn: Connection, servico_id: int) -> list[dict]:
    _require_servico(conn, servico_id)
    return conn.execute(
        """
        SELECT id, nome_arquivo, tipo, categoria, pasta, enviado_em
        FROM arquivos_dados WHERE servico_id = %s ORDER BY enviado_em, id
        """,
        (servico_id,),
    ).fetchall()


def mover_arquivo(conn: Connection, arquivo_id: int, pasta: str) -> dict:
    row = conn.execute(
        "UPDATE arquivos_dados SET pasta = %s WHERE id = %s RETURNING id, nome_arquivo, tipo, categoria, pasta, enviado_em",
        (pasta, arquivo_id),
    ).fetchone()
    if row is None:
        raise LookupError(f"arquivo {arquivo_id} não encontrado")
    return row


def caminho_arquivo(conn: Connection, arquivo_id: int, storage_dir: Path) -> tuple[str, Path]:
    row = conn.execute(
        "SELECT nome_arquivo, caminho FROM arquivos_dados WHERE id = %s", (arquivo_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"arquivo {arquivo_id} não encontrado")
    caminho = storage_dir / row["caminho"]
    if not caminho.is_file():
        raise LookupError(f"arquivo {arquivo_id} não está mais no armazenamento")
    return row["nome_arquivo"], caminho


# ---------- curvas de nível (categoria 'curvas': uma linha por cota, com propriedades {"cota": ...}) ----------

def put_curvas(conn: Connection, servico_id: int, data: CurvasIn) -> dict:
    _require_servico(conn, servico_id)
    # Valida tudo antes de apagar: uma entrada ruim não deixa as curvas vazias
    geometrias = []
    for curva in data.feicoes:
        partes = []
        for parte in curva.partes:
            if len(parte) < 2:
                raise ValueError("cada parte de curva precisa de pelo menos 2 pontos")
            partes.append(parte)
        geometrias.append((curva.cota, {"type": "MultiLineString", "coordinates": partes}))
    conn.execute(
        "DELETE FROM camadas_desenho WHERE servico_id = %s AND categoria = 'curvas'",
        (servico_id,),
    )
    for ordem, (cota, geojson) in enumerate(geometrias):
        conn.execute(
            """
            INSERT INTO camadas_desenho (servico_id, categoria, ordem, geom, propriedades)
            VALUES (%s, 'curvas', %s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4674), %s)
            """,
            (servico_id, ordem, json.dumps(geojson), json.dumps({"cota": cota, "intervalo": data.intervalo})),
        )
    return {"categoria": "curvas", "feicoes": len(geometrias), "intervalo": data.intervalo}


def apagar_curvas(conn: Connection, servico_id: int) -> dict:
    _require_servico(conn, servico_id)
    conn.execute("DELETE FROM camadas_desenho WHERE servico_id = %s AND categoria = 'curvas'", (servico_id,))
    return {"servico_id": servico_id, "curvas": "removidas"}


# ---------- base vetorial (GeoPackage) ----------

def camadas_para_gpkg(conn: Connection, servico_id: int) -> dict[str, list[dict]]:
    _require_servico(conn, servico_id)
    linhas = conn.execute(
        """
        SELECT categoria, ordem, ST_AsBinary(geom) AS wkb, propriedades
        FROM camadas_desenho WHERE servico_id = %s ORDER BY categoria, ordem
        """,
        (servico_id,),
    ).fetchall()
    vertices = conn.execute(
        """
        SELECT ordem, codigo, tipo, altitude, sigma_x, sigma_y, sigma_z,
               metodo_posicionamento AS metodo, ST_AsBinary(geom) AS wkb
        FROM vertices_sigef WHERE servico_id = %s ORDER BY ordem
        """,
        (servico_id,),
    ).fetchall()
    camadas: dict[str, list[dict]] = {}
    for row in linhas:
        atributos = {"wkb": bytes(row["wkb"]), "ordem": row["ordem"]}
        if row["categoria"] == "curvas":
            atributos["cota"] = row["propriedades"].get("cota")
            atributos["intervalo"] = row["propriedades"].get("intervalo")
        camadas.setdefault(row["categoria"], []).append(atributos)
    camadas["vertices"] = [{**v, "wkb": bytes(v["wkb"])} for v in vertices]
    return camadas



def camadas_para_sicar(conn: Connection, servico_id: int, categorias: list[str]) -> tuple[dict, dict[str, list[dict]]]:
    """Camadas do CAR com área (ha) ou comprimento (m) geodésicos e o motivo de geometria inválida."""
    servico = _require_servico(conn, servico_id)
    linhas = conn.execute(
        """
        SELECT categoria, ordem, ST_AsBinary(ST_Force2D(geom)) AS wkb,
               ST_Area(geom::geography) / 10000 AS area_ha,
               ST_Length(geom::geography) AS compr_m,
               CASE WHEN ST_IsValid(geom) THEN NULL ELSE ST_IsValidReason(geom) END AS invalida
        FROM camadas_desenho WHERE servico_id = %s AND categoria = ANY(%s) ORDER BY categoria, ordem
        """,
        (servico_id, categorias),
    ).fetchall()
    camadas: dict[str, list[dict]] = {}
    for row in linhas:
        camadas.setdefault(row["categoria"], []).append({**row, "wkb": bytes(row["wkb"])})
    return servico, camadas


# ---------- acompanhamento do CAR depois da inscrição ----------

FUSO = ZoneInfo("America/Sao_Paulo")  # o servidor roda em UTC; o "hoje" dos prazos é o de Brasília


def hoje() -> date:
    return datetime.now(FUSO).date()


def _require_pendencia(conn: Connection, pendencia_id: int) -> dict:
    row = conn.execute("SELECT * FROM car_pendencias WHERE id = %s", (pendencia_id,)).fetchone()
    if row is None:
        raise LookupError(f"pendência {pendencia_id} não encontrada")
    return row


def get_acompanhamento(conn: Connection, servico_id: int) -> dict:
    servico = _require_servico(conn, servico_id)
    prazo = servico["car_prazo"]
    return {
        "servico_id": servico_id,
        "situacao": servico["car_situacao"],
        "data": servico["car_situacao_data"],
        "prazo": prazo,
        "dias_restantes": (prazo - hoje()).days if prazo else None,
        "historico": conn.execute(
            """
            SELECT id, situacao, data, prazo, observacao FROM car_situacoes
            WHERE servico_id = %s ORDER BY data DESC, registrado_em DESC
            """,
            (servico_id,),
        ).fetchall(),
        "pendencias": conn.execute(
            """
            SELECT id, descricao, criada_em, resolvida_em FROM car_pendencias
            WHERE servico_id = %s ORDER BY resolvida_em IS NOT NULL, criada_em, id
            """,
            (servico_id,),
        ).fetchall(),
    }


def registrar_situacao(conn: Connection, servico_id: int, data: SituacaoCarIn) -> dict:
    """Muda a situação atual do CAR e guarda a mudança no histórico."""
    _require_servico(conn, servico_id)
    dia = data.data or hoje()
    prazo = data.prazo or (dia + timedelta(days=data.prazo_dias) if data.prazo_dias else None)
    if prazo and prazo < dia:
        raise ValueError("o prazo não pode ser anterior à data da situação")
    observacao = (data.observacao or "").strip() or None
    conn.execute(
        "UPDATE servicos SET car_situacao = %s, car_situacao_data = %s, car_prazo = %s WHERE id = %s",
        (data.situacao, dia, prazo, servico_id),
    )
    conn.execute(
        "INSERT INTO car_situacoes (servico_id, situacao, data, prazo, observacao) VALUES (%s, %s, %s, %s, %s)",
        (servico_id, data.situacao, dia, prazo, observacao),
    )
    return get_acompanhamento(conn, servico_id)


def criar_pendencia(conn: Connection, servico_id: int, data: PendenciaIn) -> dict:
    _require_servico(conn, servico_id)
    return conn.execute(
        """
        INSERT INTO car_pendencias (servico_id, descricao, criada_em) VALUES (%s, %s, %s)
        RETURNING id, servico_id, descricao, criada_em, resolvida_em
        """,
        (servico_id, data.descricao, hoje()),
    ).fetchone()


def atualizar_pendencia(conn: Connection, pendencia_id: int, data: PendenciaUpdate) -> dict:
    atual = _require_pendencia(conn, pendencia_id)
    descricao = data.descricao.strip() if data.descricao and data.descricao.strip() else atual["descricao"]
    resolvida_em = atual["resolvida_em"]
    if data.resolvida is not None:
        resolvida_em = (resolvida_em or hoje()) if data.resolvida else None
    return conn.execute(
        """
        UPDATE car_pendencias SET descricao = %s, resolvida_em = %s WHERE id = %s
        RETURNING id, servico_id, descricao, criada_em, resolvida_em
        """,
        (descricao, resolvida_em, pendencia_id),
    ).fetchone()


def remover_pendencia(conn: Connection, pendencia_id: int) -> dict:
    _require_pendencia(conn, pendencia_id)
    conn.execute("DELETE FROM car_pendencias WHERE id = %s", (pendencia_id,))
    return {"id": pendencia_id, "removida": True}


def prazos_car(conn: Connection, dias: int) -> list[dict]:
    """CARs aguardando complementação com prazo vencido ou vencendo nos próximos `dias`, do mais urgente."""
    dia = hoje()
    linhas = conn.execute(
        """
        SELECT s.id, s.cliente_id, c.nome AS cliente, s.nome_imovel, s.revisao, s.codigo_car, s.car_prazo AS prazo,
               (SELECT count(*) FROM car_pendencias p WHERE p.servico_id = s.id AND p.resolvida_em IS NULL)
                 AS pendencias_abertas
        FROM servicos s LEFT JOIN clientes c ON c.id = s.cliente_id
        WHERE s.car_situacao = 'aguarda_complementacao' AND s.car_prazo <= %s
          -- só a revisão mais recente do imóvel: as anteriores carregam a mesma situação copiada
          AND NOT EXISTS (
            SELECT 1 FROM servicos n
            WHERE n.empresa_id = s.empresa_id AND n.cliente_id IS NOT DISTINCT FROM s.cliente_id
              AND n.nome_imovel = s.nome_imovel AND n.revisao > s.revisao
          )
        ORDER BY s.car_prazo, s.nome_imovel
        """,
        (dia + timedelta(days=dias),),
    ).fetchall()
    return [{**linha, "dias_restantes": (linha["prazo"] - dia).days} for linha in linhas]


# ---------- conferência do projeto ----------

def dados_conferencia(conn: Connection, servico_id: int) -> tuple[dict, dict[str, dict], list[dict]]:
    """O que a conferência precisa: contagens das camadas, anexos (categoria e pasta) e o serviço."""
    servico = get_servico(conn, servico_id)
    contagem = {
        row["categoria"]: {"total": row["total"], "invalidas": row["invalidas"], "pontos": row["pontos"]}
        for row in conn.execute(
            """
            SELECT categoria, count(*) AS total,
                   count(*) FILTER (WHERE NOT ST_IsValid(geom)) AS invalidas,
                   COALESCE(sum(ST_NPoints(geom)), 0)::int AS pontos
            FROM camadas_desenho WHERE servico_id = %s GROUP BY categoria
            """,
            (servico_id,),
        ).fetchall()
    }
    anexos = conn.execute(
        "SELECT categoria, pasta FROM arquivos_dados WHERE servico_id = %s", (servico_id,)
    ).fetchall()
    return servico, contagem, anexos
