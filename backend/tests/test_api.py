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
    assert client.post(url, json={"nome_arquivo": "x.exe", "conteudo_base64": pdf}).status_code == 422
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


def test_mesmo_nome_de_imovel_em_clientes_diferentes(client, servico):
    outro = client.post("/clientes", json={"nome": "Outro Cliente", "cpf_cnpj": "111.111.111-11"}).json()
    nome = servico["nome_imovel"]
    r = client.post("/servicos", json={"cliente_id": outro["id"], "nome_imovel": nome})
    assert r.status_code == 201
    # a revisão conta por cliente: o outro cliente começa da Rev 00 e avança sozinho
    assert r.json()["revisao"] == 0
    assert client.post(f"/servicos/{r.json()['id']}/revisoes").json()["revisao"] == 1
    # no mesmo cliente o nome continua único
    repetido = client.post("/servicos", json={"cliente_id": servico["cliente_id"], "nome_imovel": nome})
    assert repetido.status_code == 409


def test_pacote_sicar_um_shapefile_por_camada(client, servico, tmp_path):
    pyogrio = pytest.importorskip("pyogrio")
    url = f"/servicos/{servico['id']}"
    assert client.get(f"{url}/exportar-sicar.zip").status_code == 422  # sem perímetro
    client.put(f"{url}/perimetro", json={"ring": QUADRADO})
    client.put(
        f"{url}/camadas",
        json={"camadas": {"sede": [SEDE], "vegetacao_nativa": [VEGETACAO], "rl_averbada": [RL_AVERBADA],
                          "edificacao": [RL_AVERBADA]}},
    )
    r = client.get(f"{url}/exportar-sicar.zip")
    assert r.status_code == 200
    pacote = zipfile.ZipFile(io.BytesIO(r.content))
    assert sorted(pacote.namelist()) == [
        "01_area_do_imovel.zip", "02_sede.zip", "03_vegetacao_nativa.zip", "11_reserva_legal_averbada.zip",
        "LEIA-ME.txt",
    ]
    leia = pacote.read("LEIA-ME.txt").decode()
    assert "EPSG:4674" in leia and "Reserva Legal averbada" in leia

    interno = zipfile.ZipFile(io.BytesIO(pacote.read("02_sede.zip")))
    assert {n.rsplit(".", 1)[1] for n in interno.namelist()} >= {"shp", "shx", "dbf", "prj"}
    interno.extractall(tmp_path)
    info = pyogrio.read_info(tmp_path / "02_sede.shp")
    assert info["geometry_type"] == "Point" and "4674" in info["crs"]

    zipfile.ZipFile(io.BytesIO(pacote.read("01_area_do_imovel.zip"))).extractall(tmp_path)
    _, _, _, campos = pyogrio.raw.read(tmp_path / "01_area_do_imovel.shp")
    assert 100 < campos[1][0] < 130  # area_ha do quadrado de 0,01° (~113 ha)


def test_situacao_do_car_com_prazo_e_historico(client, servico):
    url = f"/servicos/{servico['id']}/car"
    inicial = client.get(url).json()
    assert inicial["situacao"] == "nao_inscrito" and inicial["historico"] == [] and inicial["prazo"] is None

    client.put(f"{url}/situacao", json={"situacao": "inscrito", "data": "2026-01-10"})
    r = client.put(f"{url}/situacao", json={"situacao": "aguarda_complementacao", "data": "2026-03-01",
                                            "prazo_dias": 30, "observacao": "notificação por e-mail"})
    assert r.status_code == 200
    car = r.json()
    assert car["situacao"] == "aguarda_complementacao" and car["prazo"] == "2026-03-31"
    assert [h["situacao"] for h in car["historico"]] == ["aguarda_complementacao", "inscrito"]
    assert car["historico"][0]["observacao"] == "notificação por e-mail"

    # prazo só em 'aguarda complementação', e obrigatório nela
    assert client.put(f"{url}/situacao", json={"situacao": "aguarda_complementacao"}).status_code == 422
    assert client.put(f"{url}/situacao", json={"situacao": "aprovado", "prazo_dias": 5}).status_code == 422
    assert client.put(f"{url}/situacao", json={"situacao": "aguarda_complementacao", "data": "2026-03-01",
                                               "prazo": "2026-02-01"}).status_code == 422
    # aprovado limpa o prazo
    aprovado = client.put(f"{url}/situacao", json={"situacao": "aprovado", "data": "2026-04-02"}).json()
    assert aprovado["prazo"] is None and aprovado["dias_restantes"] is None


def test_pendencias_do_car(client, servico):
    url = f"/servicos/{servico['id']}/car"
    assert client.post(f"{url}/pendencias", json={"descricao": "   "}).status_code == 422
    p1 = client.post(f"{url}/pendencias", json={"descricao": "RL sobre APP sem justificativa"}).json()
    p2 = client.post(f"{url}/pendencias", json={"descricao": "Anexar matrícula atualizada"}).json()
    feita = client.put(f"/pendencias/{p1['id']}", json={"resolvida": True}).json()
    assert feita["resolvida_em"] is not None
    # abertas primeiro, resolvidas no fim
    assert [p["id"] for p in client.get(url).json()["pendencias"]] == [p2["id"], p1["id"]]
    assert client.put(f"/pendencias/{p1['id']}", json={"resolvida": False}).json()["resolvida_em"] is None
    assert client.delete(f"/pendencias/{p2['id']}").status_code == 200
    assert client.delete(f"/pendencias/{p2['id']}").status_code == 404


def test_alerta_de_prazos_e_revisao_leva_o_acompanhamento(client, servico):
    from datetime import date, timedelta

    sid = servico["id"]
    hoje = date.today()
    client.put(f"/servicos/{sid}/car/situacao", json={"situacao": "aguarda_complementacao",
                                                      "data": hoje.isoformat(), "prazo_dias": 10})
    client.post(f"/servicos/{sid}/car/pendencias", json={"descricao": "Corrigir sobreposição"})

    def no_alerta(dias):
        return [p for p in client.get(f"/car/prazos?dias={dias}").json() if p["nome_imovel"] == servico["nome_imovel"]]

    assert no_alerta(5) == []
    [item] = no_alerta(30)
    assert item["id"] == sid and item["pendencias_abertas"] == 1 and 9 <= item["dias_restantes"] <= 11

    nova = client.post(f"/servicos/{sid}/revisoes").json()
    car = client.get(f"/servicos/{nova['id']}/car").json()
    assert car["situacao"] == "aguarda_complementacao" and len(car["pendencias"]) == 1 and len(car["historico"]) == 1
    # o alerta mostra só a revisão mais recente
    assert [p["id"] for p in no_alerta(30)] == [nova["id"]]


def _poligono(x0, y0, x1, y1, **props):
    anel = [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [anel]}, "properties": props}


def test_sobreposicoes_com_bases_publicas(client, servico, monkeypatch):
    import httpx

    from app import consultas_publicas

    url = f"/servicos/{servico['id']}"
    assert client.get(f"{url}/sobreposicoes").status_code == 422  # sem perímetro
    client.put(f"{url}/perimetro", json={"ring": QUADRADO})  # -45,53..-45,52 x -23,04..-23,03
    meu_car = "SP-3554102-C4225CF1FD2B49A49D26315BA66897C1"
    client.put(url, json={"codigo_car": meu_car})
    chamadas = []

    def falso(caixa, caixa_ampla):
        chamadas.append((caixa, caixa_ampla))
        # vizinhos de outro município, em maior número: o MF sugerido é o do município do imóvel
        vizinhos = [_poligono(-45.60 + i * 0.001, -23.10, -45.599 + i * 0.001, -23.099,
                              cod_imovel=f"SP-X-{i}", area=32.0, m_fiscal=2.0, municipio="Pindamonhangaba")
                    for i in range(4)]
        return {
            "car": [
                _poligono(-45.530, -23.040, -45.520, -23.030, cod_imovel=meu_car, area=113.4, m_fiscal=5.67,
                          municipio="Taubaté", condicao="Aguardando análise"),
                _poligono(-45.525, -23.040, -45.515, -23.030, cod_imovel="SP-OUTRO", condicao="Aguardando análise",
                          area=100.0, m_fiscal=5.0, municipio="Taubaté"),
                _poligono(-45.52001, -23.040, -45.510, -23.030, cod_imovel="SP-DIVISA", area=80.0, m_fiscal=4.0,
                          municipio="Taubaté"),  # invade ~1 m ao longo de 1,1 km de divisa: lasca
                *vizinhos,
            ],
            "embargo_ibama": [_poligono(-45.529, -23.039, -45.528, -23.038, num_tad="123", serie_tad="C",
                                        dat_embargo=1651104000000, nome_embargado="NÃO DEVE APARECER")],
            "terra_indigena": [],
            "mineracao": httpx.ConnectTimeout("tempo esgotado"),
        }

    monkeypatch.setattr(consultas_publicas, "buscar", falso)
    r = client.get(f"{url}/sobreposicoes")
    assert r.status_code == 200
    d = r.json()
    caixa, ampla = chamadas[0]
    assert caixa == pytest.approx((-45.53, -23.04, -45.52, -23.03)) and ampla[0] < caixa[0]

    niveis = {a["texto"].split(":")[0]: a["nivel"] for a in d["achados"]}
    assert d["achados"][0]["nivel"] == "erro" and "Embargo IBAMA" in d["achados"][0]["texto"]
    assert "NÃO DEVE APARECER" not in r.text
    car = [a for a in d["achados"] if a["fonte"] == "car"]
    assert {a["nivel"] for a in car} == {"ok", "aviso"}  # o próprio CAR e o vizinho que invade metade
    outro = next(a for a in car if "SP-OUTRO" in a["texto"])
    assert 45 < outro["pct"] < 55
    assert d["toques_de_divisa"] == 1 and niveis
    fontes = {f["id"]: f for f in d["fontes"]}
    assert fontes["mineracao"]["ok"] is False and fontes["terra_indigena"]["ok"] is True
    assert d["mf_sugerido"] == {"municipio": "Taubaté", "mf_ha": 20.0, "amostras": 3}


def test_ladrilho_sicar_valida_e_guarda_em_cache(client, monkeypatch, tmp_path):
    from app import consultas_publicas
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "storage_dir", tmp_path)
    pedidos = []

    class Resposta:
        status_code = 200
        headers = {"content-type": "image/png"}
        content = b"\x89PNG-falso"

    class Cliente:
        def __init__(self, **_kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def get(self, url, params):
            pedidos.append(params)
            return Resposta()

    monkeypatch.setattr(consultas_publicas.httpx, "Client", Cliente)
    q = "layers=sicar:sicar_imoveis_sp&bbox=-5066246.23,-2632000,-5065023.24,-2630777&width=512&height=512"
    assert client.get(f"/camadas/sicar.png?{q}").content == b"\x89PNG-falso"
    assert client.get(f"/camadas/sicar.png?{q}").status_code == 200
    assert len(pedidos) == 1  # a segunda veio do cache
    assert client.get("/camadas/sicar.png?layers=outra:camada&bbox=1,2,3,4").status_code == 422
    assert client.get("/camadas/sicar.png?layers=sicar:sicar_imoveis_sp&bbox=1,2,3").status_code == 422


def test_mapa_google_sem_chave_e_com_chave(client, monkeypatch):
    from app import mapa_google
    from app.config import settings as cfg

    monkeypatch.setattr(cfg, "google_maps_key", None)
    assert client.get("/mapa/google").status_code == 404
    assert client.get("/mapa/google/ativo").json() == {"ativo": False}

    chamadas = []

    class Resp:
        def __init__(self, corpo):
            self.status_code, self._corpo = 200, corpo

        def json(self):
            return self._corpo

    def post(url, params, json, timeout):
        chamadas.append(json)
        return Resp({"session": "S1", "expiry": str(int(__import__("time").time()) + 14 * 86400), "tileWidth": 256})

    def get(url, params, timeout):
        return Resp({"copyright": "Imagens ©2026 Airbus, Maxar", "maxZoom": 21})

    monkeypatch.setattr(mapa_google.httpx, "post", post)
    monkeypatch.setattr(mapa_google.httpx, "get", get)
    monkeypatch.setattr(mapa_google, "_sessao", {})
    monkeypatch.setattr(cfg, "google_maps_key", "chave-teste")
    assert client.get("/mapa/google").json() == {"session": "S1", "key": "chave-teste", "tileSize": 256}
    client.get("/mapa/google")
    assert len(chamadas) == 1 and chamadas[0]["layerTypes"] == ["layerRoadmap"]  # sessão reaproveitada
    att = client.get("/mapa/google/atribuicao?zoom=15&norte=-23&sul=-23.1&leste=-45.4&oeste=-45.6").json()
    assert att["copyright"].startswith("Imagens")


def test_identificar_feicoes_no_ponto(client, monkeypatch):
    import httpx

    from app import identificar

    quadrado = lambda x0, y0, x1, y1: {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}

    def falso(url, params):
        if "lim_est_ibge" in url:
            return {"features": [{"properties": {"sigla": "SP"}}]}
        if url == identificar.SICAR_WFS:
            assert params["typeNames"] == "sicar:sicar_imoveis_sp"
            return {"features": [
                {"properties": {"cod_imovel": "SP-1-AAA", "tipo_imovel": "IRU", "status_imovel": "AT", "area": 72.5635,
                                "m_fiscal": 3.6282, "dat_criacao": "2023-01-27T02:21:52.540Z"},
                 "geometry": quadrado(-45.6, -23.1, -45.4, -22.9)},
                # caixa envolvente toca o ponto, mas o polígono não o contém
                {"properties": {"cod_imovel": "SP-1-BBB"}, "geometry": quadrado(-45.49, -23.1, -45.4, -22.9)},
            ]}
        if "SIGMINE" in url:
            assert "NOME" not in params["outFields"]  # titular não é pedido
            return {"features": [{"properties": {"PROCESSO": "820601/1988", "NOME": "Fulano", "AREA_HA": 907.18},
                                  "geometry": quadrado(-46, -24, -45, -22)}]}
        raise httpx.ConnectError("fora do ar")

    monkeypatch.setattr(identificar, "_get_json", falso)
    r = client.get("/camadas/identificar?lat=-23.0&lng=-45.5&camadas=sicar,mineracao,terra_indigena&grau_px=0.00002")
    assert r.status_code == 200, r.text
    sicar, anm, ti = r.json()["camadas"]
    assert [f["titulo"] for f in sicar["feicoes"]] == ["SP-1-AAA"]
    campos = dict(sicar["feicoes"][0]["campos"])
    assert campos["Tipo"] == "Imóvel rural (IRU)"
    assert campos["Área declarada"] == "72,5635 ha"
    assert campos["Inscrito em"] == "27/01/2023"
    assert sicar["feicoes"][0]["geometry"]["type"] == "Polygon"
    assert "Fulano" not in str(anm) and dict(anm["feicoes"][0]["campos"])["Área"] == "907,1800 ha"
    assert ti == {"id": "terra_indigena", "nome": "Terras indígenas (FUNAI)", "ok": False,
                  "erro": "fonte indisponível no momento", "feicoes": []}

    assert client.get("/camadas/identificar?lat=-23&lng=-45.5&camadas=xyz").status_code == 422
    assert client.get("/camadas/identificar?lat=40&lng=-45.5&camadas=sicar").status_code == 422


def test_identificar_nao_le_responsavel_tecnico_do_sigef():
    from app import identificar

    for camada in ("sigef", "sigef_publico", "sigef_sp_2024"):
        campos = {campo for campo, *_ in identificar.CAMADAS[camada][3]}
        assert not campos & {"rt", "art"}, camada


def _anexo(client, servico_id, nome, conteudo, pasta="documentos"):
    return client.post(f"/servicos/{servico_id}/arquivos", json={
        "nome_arquivo": nome, "pasta": pasta, "conteudo_base64": base64.b64encode(conteudo).decode(),
    })


def test_anexos_de_projeto_tipos_e_pastas(client, servico, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    sid = servico["id"]
    dxf = b"0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n"
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    planta = _anexo(client, sid, "planta-geral.dxf", dxf, pasta="plantas")
    assert planta.status_code == 201, planta.text
    assert planta.json()["tipo"] == "DXF" and planta.json()["pasta"] == "plantas"
    foto = _anexo(client, sid, "vista.png", png, pasta="imagens")
    assert foto.status_code == 201 and foto.json()["tipo"] == "PNG"

    # o conteúdo precisa bater com a extensão
    assert _anexo(client, sid, "falso.png", b"texto qualquer").status_code == 422
    assert _anexo(client, sid, "planilha.xlsx", b"%PDF-1.4").status_code == 422
    assert _anexo(client, sid, "x.exe", b"MZ").status_code == 422
    assert _anexo(client, sid, "ok.pdf", PDF_MINIMO, pasta="pasta-inventada").status_code == 422

    movido = client.put(f"/arquivos/{planta.json()['id']}", json={"pasta": "memoriais"})
    assert movido.status_code == 200 and movido.json()["pasta"] == "memoriais"
    assert client.put(f"/arquivos/{planta.json()['id']}", json={"pasta": "nenhuma"}).status_code == 422

    baixado = client.get(f"/arquivos/{foto.json()['id']}")
    assert baixado.headers["content-type"] == "image/png"


def test_pacote_do_projeto_organiza_anexos_por_pasta(client, servico, tmp_path, monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    sid = servico["id"]
    _anexo(client, sid, "memorial.pdf", PDF_MINIMO, pasta="memoriais")
    _anexo(client, sid, "memorial.pdf", PDF_MINIMO, pasta="memoriais")  # mesmo nome: não se perde
    _anexo(client, sid, "matricula.pdf", PDF_MINIMO)

    resposta = client.get(f"/servicos/{sid}/pacote.zip")
    assert resposta.status_code == 200, resposta.text
    assert resposta.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resposta.content)) as z:
        nomes = z.namelist()
        assert "memoriais/memorial.pdf" in nomes and "memoriais/memorial (2).pdf" in nomes
        assert "documentos/matricula.pdf" in nomes
        leia = z.read("LEIA-ME.txt").decode("utf-8")
    assert "memoriais/ (Memoriais, 2 arquivo(s))" in leia
    assert "Sem memorial descritivo" not in leia  # há memorial no projeto


def test_conferencia_do_projeto_responde_itens(client, servico, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    _anexo(client, servico["id"], "matricula.pdf", PDF_MINIMO, pasta="documentos")
    resposta = client.get(f"/servicos/{servico['id']}/conferencia")
    assert resposta.status_code == 200, resposta.text
    itens = resposta.json()["itens"]
    assert {i["grupo"] for i in itens} <= {"Georreferenciamento", "CAR"}
    assert any("Perímetro não desenhado" in i["texto"] for i in itens)
    assert all({"grupo", "nivel", "texto", "base"} <= set(i) for i in itens)


def test_relatorio_em_csv_e_txt_so_aceita_texto(client, servico, tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    sid = servico["id"]
    assert _anexo(client, sid, "quadro-de-areas-car.csv", "Item;Área\nTotal;1,0000\n".encode("utf-8"),
                  pasta="relatorios").json()["tipo"] == "CSV"
    assert _anexo(client, sid, "pre-analise-car.txt", "✓ Sede marcada.".encode("utf-8"),
                  pasta="relatorios").json()["tipo"] == "TXT"
    assert _anexo(client, sid, "binario.txt", b"\xff\xfe\x00\x01").status_code == 422


def test_pacote_avisa_quando_falta_memorial(client, servico, tmp_path, monkeypatch):
    import io
    import zipfile

    monkeypatch.setattr(settings, "storage_dir", tmp_path)
    _anexo(client, servico["id"], "matricula.pdf", PDF_MINIMO)
    with zipfile.ZipFile(io.BytesIO(client.get(f"/servicos/{servico['id']}/pacote.zip").content)) as z:
        assert "Sem memorial descritivo" in z.read("LEIA-ME.txt").decode("utf-8")
