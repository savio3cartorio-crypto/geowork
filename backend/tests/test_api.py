"""Testes de integração: exigem um PostgreSQL com PostGIS acessível em GEOWORK_DATABASE_URL.

Sem banco, o módulo é pulado. Rodar a partir da pasta backend/:
    GEOWORK_DATABASE_URL=postgresql://geowork:...@localhost/geowork .venv/bin/pytest -q
"""

import base64
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402

try:
    psycopg.connect(settings.database_url, connect_timeout=2).close()
except psycopg.OperationalError:
    pytest.skip("banco PostgreSQL/PostGIS indisponível", allow_module_level=True)

from app.main import app  # noqa: E402

# Quadrado de ~1 km em Taubaté-SP (zona 23S), em graus
QUADRADO = [[-45.530, -23.040], [-45.520, -23.040], [-45.520, -23.030], [-45.530, -23.030]]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def servico(client):
    cliente = client.post("/clientes", json={"nome": "Teste Fazenda", "cpf_cnpj": "000.000.000-00"}).json()
    nome = f"Sitio Teste {uuid.uuid4().hex[:8]}"
    srv = client.post("/servicos", json={"cliente_id": cliente["id"], "nome_imovel": nome}).json()
    yield srv
    # Limpeza: remove o serviço (e as revisões com o mesmo nome) e o cliente criados pelo teste
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("DELETE FROM servicos WHERE nome_imovel = %s", (nome,))
        conn.execute("DELETE FROM clientes WHERE id = %s", (cliente["id"],))


def test_health(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["postgis"]


def test_perimetro_gera_vertices_com_codigo_padrao(client, servico):
    sid = servico["id"]
    r = client.put(f"/servicos/{sid}/perimetro", json={"ring": QUADRADO})
    assert r.status_code == 200, r.text
    assert r.json()["vertices"] == 4

    detalhe = client.get(f"/servicos/{sid}").json()
    codigos = [v["codigo"] for v in detalhe["vertices"]]
    assert codigos[0] == "SITI-M-0001"
    assert codigos[1] == "SITI-V-0002"


def test_confrontante_liga_trecho_ao_vertice_seguinte(client, servico):
    sid = servico["id"]
    payload = {
        "ring": QUADRADO,
        "confrontantes": [{"indice": 1, "nome": "Vizinho do trecho 2", "tipo_divisa": "ESTRADA"}],
    }
    assert client.put(f"/servicos/{sid}/perimetro", json=payload).status_code == 200
    confr = client.get(f"/servicos/{sid}").json()["confrontantes"]
    assert len(confr) == 1
    assert confr[0]["indice"] == 1
    assert confr[0]["nome"] == "Vizinho do trecho 2"


def test_offset_positivo_expande_poligono(client, servico):
    sid = servico["id"]
    client.put(f"/servicos/{sid}/perimetro", json={"ring": QUADRADO})
    client.put(
        f"/servicos/{sid}/camadas/rl",
        json={"features": [QUADRADO]},
    )
    r = client.post(f"/servicos/{sid}/offset", json={"categoria": "rl", "indice": 0, "distancia_m": 50})
    assert r.status_code == 200, r.text
    nova = r.json()["geometry"]
    lngs = [p[0] for p in nova["coordinates"][0]]
    # 50 m a ~-23° equivalem a ~0,0005° de longitude: o contorno cresce para os dois lados
    assert min(lngs) < -45.530
    assert max(lngs) > -45.520
    # a feição original continua presente
    camadas = client.get(f"/servicos/{sid}").json()["camadas"]
    assert len([c for c in camadas if c["categoria"] == "rl"]) == 2


def test_offset_negativo_contrai_poligono(client, servico):
    sid = servico["id"]
    client.put(f"/servicos/{sid}/camadas/app", json={"features": [QUADRADO]})
    r = client.post(f"/servicos/{sid}/offset", json={"categoria": "app", "indice": 0, "distancia_m": -100})
    assert r.status_code == 200, r.text
    lngs = [p[0] for p in r.json()["geometry"]["coordinates"][0]]
    assert min(lngs) > -45.530
    assert max(lngs) < -45.520


def test_offset_sobre_perimetro_e_recusado(client, servico):
    sid = servico["id"]
    client.put(f"/servicos/{sid}/perimetro", json={"ring": QUADRADO})
    r = client.post(f"/servicos/{sid}/offset", json={"categoria": "perimetro", "indice": 0, "distancia_m": 5})
    assert r.status_code == 422


def test_revisao_copia_vertices_e_confrontantes(client, servico):
    sid = servico["id"]
    client.put(
        f"/servicos/{sid}/perimetro",
        json={"ring": QUADRADO, "confrontantes": [{"indice": 0, "nome": "Vizinho A"}]},
    )
    nova = client.post(f"/servicos/{sid}/revisoes").json()
    assert nova["revisao"] == servico["revisao"] + 1
    assert len(nova["vertices"]) == 4
    assert [c["nome"] for c in nova["confrontantes"]] == ["Vizinho A"]


def test_servico_inexistente_retorna_404(client):
    assert client.get("/servicos/999999999").status_code == 404


def test_coordenada_fora_do_brasil_no_offset(client, servico):
    sid = servico["id"]
    fora = [[10.0, 50.0], [10.01, 50.0], [10.01, 50.01], [10.0, 50.01]]
    client.put(f"/servicos/{sid}/camadas/app", json={"features": [fora]})
    r = client.post(f"/servicos/{sid}/offset", json={"categoria": "app", "indice": 0, "distancia_m": 5})
    assert r.status_code == 422


def test_atualizar_dados_do_servico(client, servico):
    sid = servico["id"]
    r = client.put(
        f"/servicos/{sid}",
        json={"matricula": "45.120", "codigo_car": "SP-3554102-XYZ", "codigo_sncr": "634.085.012.345-8", "modulo_fiscal_ha": 15.5},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matricula"] == "45.120"
    assert float(body["modulo_fiscal_ha"]) == 15.5


def test_remover_perimetro_apaga_vertices_e_confrontantes(client, servico):
    sid = servico["id"]
    client.put(
        f"/servicos/{sid}/perimetro",
        json={"ring": QUADRADO, "confrontantes": [{"indice": 0, "nome": "Vizinho"}]},
    )
    assert client.delete(f"/servicos/{sid}/perimetro").status_code == 200
    detalhe = client.get(f"/servicos/{sid}").json()
    assert detalhe["vertices"] == []
    assert detalhe["confrontantes"] == []
    assert not [c for c in detalhe["camadas"] if c["categoria"] == "perimetro"]


def test_interface_servida_na_mesma_origem(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "GeoWork" in r.text
    assert client.get("/js/app.js").status_code == 200


PDF_MINIMO = b"%PDF-1.4\n%%EOF\n"


def test_arquivo_pdf_envia_lista_e_baixa(client, servico, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    corpo = {
        "nome_arquivo": "demonstrativo-car.pdf",
        "categoria": "recibo_car",
        "conteudo_base64": base64.b64encode(PDF_MINIMO).decode(),
    }
    criado = client.post(f"/servicos/{servico['id']}/arquivos", json=corpo)
    assert criado.status_code == 201
    arquivo_id = criado.json()["id"]

    lista = client.get(f"/servicos/{servico['id']}/arquivos").json()
    assert [a["id"] for a in lista] == [arquivo_id]
    assert lista[0]["categoria"] == "recibo_car"

    baixado = client.get(f"/arquivos/{arquivo_id}")
    assert baixado.status_code == 200
    assert baixado.content == PDF_MINIMO


def test_arquivo_recusa_nao_pdf(client, servico, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    url = f"/servicos/{servico['id']}/arquivos"
    texto = base64.b64encode(b"nao sou pdf").decode()
    assert client.post(url, json={"nome_arquivo": "x.pdf", "conteudo_base64": texto}).status_code == 422
    pdf = base64.b64encode(PDF_MINIMO).decode()
    assert client.post(url, json={"nome_arquivo": "x.txt", "conteudo_base64": pdf}).status_code == 422
    assert client.post(url, json={"nome_arquivo": "x.pdf", "conteudo_base64": "!!!"}).status_code == 422
