"""Testes de integração: exigem um PostgreSQL com PostGIS acessível em GEOWORK_DATABASE_URL.

Sem banco, o módulo é pulado. Rodar a partir da pasta backend/:
    GEOWORK_DATABASE_URL=postgresql://geowork:...@localhost/geowork .venv/bin/pytest -q
"""

import base64
import io
import os
import uuid
import zipfile

# Cookie de sessão sem Secure: o TestClient usa http://testserver
os.environ.setdefault("GEOWORK_COOKIE_SEGURO", "false")

import pytest

psycopg = pytest.importorskip("psycopg")
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import hash_senha, verificar_senha  # noqa: E402
from app.config import settings  # noqa: E402

try:
    psycopg.connect(settings.database_url, connect_timeout=2).close()
except psycopg.OperationalError:
    pytest.skip("banco PostgreSQL/PostGIS indisponível", allow_module_level=True)

from app.main import app  # noqa: E402

# Quadrado de ~1 km em Taubaté-SP (zona 23S), em graus
QUADRADO = [[-45.530, -23.040], [-45.520, -23.040], [-45.520, -23.030], [-45.530, -23.030]]


SENHA_TESTE = "senha-de-teste-123"


@pytest.fixture(scope="module")
def usuario_teste():
    email = f"teste-{uuid.uuid4().hex[:8]}@geowork.test"
    with psycopg.connect(settings.database_url) as conn:
        conn.execute(
            "INSERT INTO usuarios (email, senha_hash, papel) VALUES (%s, %s, 'master')",
            (email, hash_senha(SENHA_TESTE)),
        )
    yield email
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("DELETE FROM usuarios WHERE email = %s", (email,))


@pytest.fixture(scope="module")
def client(usuario_teste):
    with TestClient(app) as c:
        r = c.post("/auth/login", json={"email": usuario_teste, "senha": SENHA_TESTE})
        assert r.status_code == 200, r.text
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
        json={"matricula": "45.120", "codigo_car": "sp-3554102-0123456789abcdef0123456789abcdef", "codigo_sncr": "634.085.012.345-8", "modulo_fiscal_ha": 15.5},
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


def test_codigo_car_fora_do_formato_recusado(client, servico):
    url = f"/servicos/{servico['id']}"
    ruim = client.put(url, json={"codigo_car": "SP-3554102-XYZ"})
    assert ruim.status_code == 422
    ok = client.put(url, json={"codigo_car": "sp-3554102-0123456789abcdef0123456789abcdef"})
    assert ok.status_code == 200
    assert ok.json()["codigo_car"] == "SP-3554102-0123456789ABCDEF0123456789ABCDEF"


def test_busca_por_car_e_nome(client, servico):
    car = "sp-3554102-fedcba9876543210fedcba9876543210"
    client.put(f"/servicos/{servico['id']}", json={"codigo_car": car, "matricula": "M-98765"})
    achados = client.get("/buscar", params={"q": "FEDCBA9876543210"}).json()
    assert [a["id"] for a in achados] == [servico["id"]]
    assert achados[0]["codigo_car"] == car.upper()
    por_matricula = client.get("/buscar", params={"q": "M-98765"}).json()
    assert any(a["id"] == servico["id"] for a in por_matricula)
    assert client.get("/buscar", params={"q": "x"}).status_code == 422
    assert client.get("/buscar", params={"q": "nao-existe-jamais-123"}).json() == []


def test_memorial_docx_gera_arquivo_word(client):
    corpo = {"titulo": "Memorial Teste", "formato": "corrido", "linhas": ["Inicia-se no vértice ABCD-M-0001.", "Confrontante: Fulano."]}
    resposta = client.post("/memorial.docx", json=corpo)
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert resposta.content[:2] == b"PK"  # .docx é um zip
    with zipfile.ZipFile(io.BytesIO(resposta.content)) as z:
        texto = z.read("word/document.xml").decode("utf-8")
    assert "Inicia-se no vértice ABCD-M-0001." in texto


def test_curvas_salvam_com_cota_e_podem_ser_removidas(client, servico):
    url = f"/servicos/{servico['id']}"
    corpo = {
        "intervalo": 1,
        "feicoes": [
            {"cota": 500, "partes": [[[-45.529, -23.035], [-45.528, -23.034]]]},
            {"cota": 501, "partes": [[[-45.527, -23.036], [-45.526, -23.037]], [[-45.525, -23.03], [-45.524, -23.031]]]},
        ],
    }
    salvo = client.put(f"{url}/curvas", json=corpo)
    assert salvo.status_code == 200
    assert salvo.json()["feicoes"] == 2

    camadas = [c for c in client.get(url).json()["camadas"] if c["categoria"] == "curvas"]
    assert sorted(c["propriedades"]["cota"] for c in camadas) == [500, 501]
    assert camadas[1]["geometry"]["type"] == "MultiLineString"
    assert len(camadas[1]["geometry"]["coordinates"]) == 2

    # uma parte com um único ponto não é uma linha: recusa e não apaga as curvas anteriores
    ruim = {"intervalo": 1, "feicoes": [{"cota": 600, "partes": [[[-45.5, -23.0]]]}]}
    assert client.put(f"{url}/curvas", json=ruim).status_code == 422
    assert len([c for c in client.get(url).json()["camadas"] if c["categoria"] == "curvas"]) == 2

    assert client.delete(f"{url}/curvas").status_code == 200
    assert not [c for c in client.get(url).json()["camadas"] if c["categoria"] == "curvas"]


def test_exportar_gpkg_tem_camadas_em_sirgas(client, servico, tmp_path):
    pyogrio = pytest.importorskip("pyogrio")
    url = f"/servicos/{servico['id']}"
    client.put(f"{url}/perimetro", json={"ring": QUADRADO})
    client.put(f"{url}/curvas", json={"intervalo": 1, "feicoes": [{"cota": 500, "partes": [[[-45.529, -23.035], [-45.528, -23.034]]]}]})

    resposta = client.get(f"{url}/exportar.gpkg")
    assert resposta.status_code == 200
    assert resposta.headers["content-type"].startswith("application/geopackage")
    assert resposta.content[:16] == b"SQLite format 3\x00"

    arquivo = tmp_path / "base.gpkg"
    arquivo.write_bytes(resposta.content)
    nomes = {nome for nome, _ in pyogrio.list_layers(arquivo)}
    assert {"perimetro", "vertices", "curvas"} <= nomes
    info = pyogrio.read_info(arquivo, layer="perimetro")
    assert info["crs"] == "EPSG:4674"
    assert info["features"] == 1
    assert pyogrio.read_info(arquivo, layer="vertices")["features"] == 4


def test_endereco_do_cliente_e_padronizado(client):
    corpo = {
        "nome": "Endereco Teste", "cpf_cnpj": "000.000.000-00",
        "cep": "01001000", "logradouro": "  Praça   da Sé ", "numero": "S/N", "uf": "sp",
        "municipio": "São Paulo", "bairro": "Sé",
    }
    criado = client.post("/clientes", json=corpo)
    assert criado.status_code == 201
    cliente = criado.json()
    assert cliente["cep"] == "01001-000"
    assert cliente["uf"] == "SP"
    assert cliente["logradouro"] == "Praça da Sé"

    assert client.post("/clientes", json={**corpo, "cep": "0100-1"}).status_code == 422
    assert client.post("/clientes", json={**corpo, "uf": "XX"}).status_code == 422

    atualizado = client.put(f"/clientes/{cliente['id']}", json={**corpo, "numero": "100", "uf": "RJ"})
    assert atualizado.status_code == 200
    assert atualizado.json()["numero"] == "100"
    assert atualizado.json()["uf"] == "RJ"
    assert client.put("/clientes/999999999", json=corpo).status_code == 404

    import psycopg
    with psycopg.connect(settings.database_url) as conn:
        conn.execute("DELETE FROM clientes WHERE id = %s", (cliente["id"],))


# ---------- login ----------

def test_senha_guardada_como_hash_e_verificavel():
    h = hash_senha(SENHA_TESTE)
    assert h.startswith("scrypt$") and SENHA_TESTE not in h
    assert verificar_senha(SENHA_TESTE, h)
    assert not verificar_senha("outra-senha-qualquer", h)
    assert not verificar_senha(SENHA_TESTE, "lixo-sem-formato")


def test_api_exige_login():
    anonimo = TestClient(app)
    assert anonimo.get("/servicos").status_code == 401
    assert anonimo.post("/clientes", json={"nome": "Sem login"}).status_code == 401
    assert anonimo.get("/auth/eu").status_code == 401
    assert anonimo.get("/health").status_code == 200


def test_pagina_principal_redireciona_para_login():
    anonimo = TestClient(app)
    r = anonimo.get("/", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/login"
    assert anonimo.get("/login").status_code == 200


def test_senha_errada_recusada(usuario_teste):
    anonimo = TestClient(app)
    r = anonimo.post("/auth/login", json={"email": usuario_teste, "senha": "errada-errada-1"})
    assert r.status_code == 401
    assert "gw_sessao" not in anonimo.cookies


def test_login_logout_revoga_sessao(usuario_teste):
    c = TestClient(app)
    r = c.post("/auth/login", json={"email": usuario_teste.upper(), "senha": SENHA_TESTE})
    assert r.status_code == 200, r.text
    assert r.json()["papel"] == "master"
    cookie = c.cookies.get("gw_sessao")
    assert cookie
    assert c.get("/auth/eu").status_code == 200

    assert c.post("/auth/logout").status_code == 200
    assert c.get("/auth/eu").status_code == 401

    # Um cookie copiado antes do logout não vale mais: a sessão foi apagada no servidor
    c.cookies.set("gw_sessao", cookie)
    assert c.get("/auth/eu").status_code == 401


def test_bloqueia_apos_tentativas_erradas():
    email = f"bloqueio-{uuid.uuid4().hex[:8]}@geowork.test"
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/auth/login", json={"email": email, "senha": "errada-errada-1"}).status_code == 401
    assert c.post("/auth/login", json={"email": email, "senha": "errada-errada-1"}).status_code == 429


# ---------- camadas no padrão do CAR ----------

# Dentro do QUADRADO: metade oeste com vegetação nativa e uma faixa de RL averbada
VEGETACAO = [[-45.530, -23.040], [-45.525, -23.040], [-45.525, -23.030], [-45.530, -23.030]]
RL_AVERBADA = [[-45.530, -23.040], [-45.528, -23.040], [-45.528, -23.030], [-45.530, -23.030]]
SEDE = [[-45.522, -23.035]]


def test_camadas_car_em_lote_com_sede_em_ponto(client, servico):
    url = f"/servicos/{servico['id']}"
    r = client.put(f"{url}/camadas", json={"camadas": {
        "sede": [SEDE], "vegetacao_nativa": [VEGETACAO], "rl_averbada": [RL_AVERBADA],
    }})
    assert r.status_code == 200, r.text
    assert r.json()["camadas"] == {"sede": 1, "vegetacao_nativa": 1, "rl_averbada": 1}

    camadas = {c["categoria"]: c["geometry"] for c in client.get(url).json()["camadas"]}
    assert camadas["sede"]["type"] == "Point"
    assert camadas["sede"]["coordinates"] == SEDE[0]
    assert camadas["vegetacao_nativa"]["type"] == "Polygon"
    assert camadas["rl_averbada"]["type"] == "Polygon"


def test_imovel_tem_uma_so_sede(client, servico):
    r = client.put(f"/servicos/{servico['id']}/camadas", json={"camadas": {"sede": [SEDE, [[-45.521, -23.034]]]}})
    assert r.status_code == 422
    assert "uma só sede" in r.json()["detail"]


def test_lote_de_camadas_e_atomico(client, servico):
    url = f"/servicos/{servico['id']}"
    r = client.put(f"{url}/camadas", json={"camadas": {
        "vegetacao_nativa": [VEGETACAO], "pousio": [[[-45.53, -23.04], [-45.52, -23.04]]],
    }})
    assert r.status_code == 422
    # A vegetação válida não foi gravada, porque o pousio inválido desfez a transação inteira
    assert client.get(url).json()["camadas"] == []


def test_categoria_desconhecida_recusada(client, servico):
    r = client.put(f"/servicos/{servico['id']}/camadas", json={"camadas": {"floresta": [VEGETACAO]}})
    assert r.status_code == 422


def test_dados_car_do_servico_e_revisao_copia(client, servico):
    url = f"/servicos/{servico['id']}"
    r = client.put(url, json={"modulo_fiscal_ha": 20, "area_documental_ha": 98.5, "rl_percentual": 20})
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert float(corpo["area_documental_ha"]) == 98.5
    assert float(corpo["rl_percentual"]) == 20

    assert client.put(url, json={"rl_percentual": 150}).status_code == 422
    assert client.put(url, json={"area_documental_ha": 0}).status_code == 422

    nova = client.post(f"{url}/revisoes").json()
    assert float(nova["modulo_fiscal_ha"]) == 20
    assert float(nova["area_documental_ha"]) == 98.5
    assert float(nova["rl_percentual"]) == 20


def test_gpkg_inclui_camadas_car(client, servico, tmp_path):
    pyogrio = pytest.importorskip("pyogrio")
    url = f"/servicos/{servico['id']}"
    client.put(f"{url}/perimetro", json={"ring": QUADRADO})
    client.put(f"{url}/camadas", json={"camadas": {"sede": [SEDE], "vegetacao_nativa": [VEGETACAO]}})
    arquivo = tmp_path / "base.gpkg"
    arquivo.write_bytes(client.get(f"{url}/exportar.gpkg").content)
    nomes = {nome for nome, _ in pyogrio.list_layers(arquivo)}
    assert {"perimetro", "sede", "vegetacao_nativa"} <= nomes
    assert pyogrio.read_info(arquivo, layer="sede")["geometry_type"] == "Point"
