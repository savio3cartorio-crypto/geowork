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
from pathlib import Path
from typing import Any

from psycopg import Connection

from .schemas import ArquivoIn, ClienteIn, ConfrontanteIn, ServicoIn, VerticeMeta

CATEGORIAS_FECHADAS = {"rl", "app", "edificacao"}  # polígonos
CATEGORIAS_LINEARES = {"hidro"}  # linhas
CATEGORIAS_OFFSET = CATEGORIAS_FECHADAS | CATEGORIAS_LINEARES

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
        """
        INSERT INTO clientes (empresa_id, nome, cpf_cnpj, email, telefone)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, nome, cpf_cnpj, email, telefone
        """,
        (empresa_id, data.nome, data.cpf_cnpj, data.email, data.telefone),
    ).fetchone()


def list_clientes(conn: Connection) -> list[dict]:
    return conn.execute("SELECT id, nome, cpf_cnpj, email, telefone FROM clientes ORDER BY nome").fetchall()


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


def get_servico(conn: Connection, servico_id: int) -> dict:
    servico = _require_servico(conn, servico_id)
    camadas = conn.execute(
        """
        SELECT categoria, ordem, ST_AsGeoJSON(geom)::json AS geometry
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
        "SELECT COALESCE(MAX(revisao), -1) + 1 AS r FROM servicos WHERE empresa_id = %s AND nome_imovel = %s",
        (origem["empresa_id"], origem["nome_imovel"]),
    ).fetchone()["r"]

    novo = conn.execute(
        """
        INSERT INTO servicos (empresa_id, cliente_id, rt_id, tipo, nome_imovel, codigo_car, codigo_sncr,
                              matricula, revisao, servico_origem_id)
        SELECT empresa_id, cliente_id, rt_id, tipo, nome_imovel, codigo_car, codigo_sncr,
               matricula, %(rev)s, id
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
    if categoria not in CATEGORIAS_OFFSET:
        raise ValueError(f"categoria '{categoria}' não aceita aqui; o perímetro tem rota própria")
    # Valida tudo antes de apagar: uma entrada ruim não deixa a camada vazia
    geometrias = [
        _polygon(points) if categoria in CATEGORIAS_FECHADAS else _linestring(points)
        for points in features
    ]
    conn.execute(
        "DELETE FROM camadas_desenho WHERE servico_id = %s AND categoria = %s",
        (servico_id, categoria),
    )
    for ordem, geojson in enumerate(geometrias):
        _insert_feature(conn, servico_id, categoria, ordem, geojson)
    return {"categoria": categoria, "feicoes": len(geometrias)}


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
        SET matricula = %s, codigo_car = %s, codigo_sncr = %s, modulo_fiscal_ha = %s
        WHERE id = %s
        """,
        (data.matricula, data.codigo_car, data.codigo_sncr, data.modulo_fiscal_ha, servico_id),
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


# ---------- arquivos do serviço (PDFs: demonstrativo do CAR, matrícula...) ----------

LIMITE_ARQUIVO_BYTES = 10 * 1024 * 1024


def salvar_arquivo(conn: Connection, servico_id: int, data: ArquivoIn, storage_dir: Path) -> dict:
    _require_servico(conn, servico_id)
    if not data.nome_arquivo.lower().endswith(".pdf"):
        raise ValueError("apenas arquivos PDF são aceitos")
    try:
        conteudo = base64.b64decode(data.conteudo_base64, validate=True)
    except binascii.Error as exc:
        raise ValueError("conteúdo do arquivo não está em base64 válido") from exc
    if not conteudo.startswith(b"%PDF"):
        raise ValueError("o conteúdo não é um PDF")
    if len(conteudo) > LIMITE_ARQUIVO_BYTES:
        raise ValueError("arquivo maior que 10 MB")

    relativo = f"{servico_id}/{uuid.uuid4().hex}.pdf"
    destino = storage_dir / relativo
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(conteudo)

    return conn.execute(
        """
        INSERT INTO arquivos_dados (servico_id, nome_arquivo, tipo, categoria, caminho)
        VALUES (%s, %s, 'PDF', %s, %s)
        RETURNING id, nome_arquivo, categoria, enviado_em
        """,
        (servico_id, data.nome_arquivo, data.categoria, relativo),
    ).fetchone()


def listar_arquivos(conn: Connection, servico_id: int) -> list[dict]:
    _require_servico(conn, servico_id)
    return conn.execute(
        """
        SELECT id, nome_arquivo, categoria, enviado_em
        FROM arquivos_dados WHERE servico_id = %s ORDER BY enviado_em, id
        """,
        (servico_id,),
    ).fetchall()


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
