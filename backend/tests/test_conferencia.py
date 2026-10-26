from app.conferencia import checar

SERVICO_COMPLETO = {
    "codigo_car": "SP-3554102-E4A558DDFC5649D0A3524EB344ECC52D", "codigo_sncr": "6351200038168",
    "matricula": "38819", "area_documental_ha": 21.9, "modulo_fiscal_ha": 20.0, "car_situacao": "inscrito",
}
ANEXOS_COMPLETOS = [
    {"categoria": "matricula", "pasta": "documentos"}, {"categoria": "outro", "pasta": "plantas"},
    {"categoria": "outro", "pasta": "memoriais"}, {"categoria": "art", "pasta": "documentos"},
    {"categoria": "recibo_car", "pasta": "documentos"},
]
CONTAGEM_OK = {"perimetro": {"total": 1, "invalidas": 0, "pontos": 5}, "sede": {"total": 1, "invalidas": 0, "pontos": 1}}


def por_texto(itens, trecho):
    return [i for i in itens if trecho in i["texto"]]


def test_projeto_completo_so_tem_ok_e_manual():
    vertices = [{"indice": i, "sigma_x": 0.01, "sigma_y": 0.01} for i in range(4)]
    confrontantes = [{"indice": i} for i in range(4)]
    itens = checar(SERVICO_COMPLETO, vertices, confrontantes, CONTAGEM_OK, ANEXOS_COMPLETOS)
    niveis = {i["nivel"] for i in itens}
    assert niveis <= {"ok", "manual"}, [i for i in itens if i["nivel"] not in ("ok", "manual")]
    assert len([i for i in itens if i["nivel"] == "manual"]) == 3


def test_perimetro_ausente_e_invalido_sao_erro():
    itens = checar(SERVICO_COMPLETO, [], [], {}, ANEXOS_COMPLETOS)
    assert por_texto(itens, "Perímetro não desenhado")[0]["nivel"] == "erro"
    invalido = {"perimetro": {"total": 1, "invalidas": 1, "pontos": 5}}
    itens = checar(SERVICO_COMPLETO, [], [], invalido, ANEXOS_COMPLETOS)
    assert por_texto(itens, "geometria inválida")[0]["nivel"] == "erro"


def test_poucos_vertices_e_erro():
    itens = checar(SERVICO_COMPLETO, [], [], {"perimetro": {"total": 1, "invalidas": 0, "pontos": 3}}, ANEXOS_COMPLETOS)
    assert por_texto(itens, "Apenas 2 vértice(s)")[0]["nivel"] == "erro"


def test_confrontante_e_precisao_faltando_viram_aviso():
    vertices = [{"indice": 0, "sigma_x": None, "sigma_y": 0.02}, {"indice": 1, "sigma_x": 0.01, "sigma_y": 0.01}]
    confrontantes = [{"indice": 0}]
    itens = checar(SERVICO_COMPLETO, vertices, confrontantes, CONTAGEM_OK, ANEXOS_COMPLETOS)
    assert por_texto(itens, "sem confrontante")[0]["nivel"] == "aviso"
    assert por_texto(itens, "sem precisão")[0]["nivel"] == "aviso"


def test_codigo_sncr_precisa_de_13_digitos():
    com_pontuacao = {**SERVICO_COMPLETO, "codigo_sncr": "635.120.003.816-8"}  # 13 dígitos com pontuação
    assert por_texto(checar(com_pontuacao, [], [], CONTAGEM_OK, ANEXOS_COMPLETOS), "13 dígitos")[0]["nivel"] == "ok"
    curto = {**SERVICO_COMPLETO, "codigo_sncr": "123"}
    assert por_texto(checar(curto, [], [], CONTAGEM_OK, ANEXOS_COMPLETOS), "Código SNCR com 3")[0]["nivel"] == "erro"
    vazio = {**SERVICO_COMPLETO, "codigo_sncr": None}
    assert por_texto(checar(vazio, [], [], CONTAGEM_OK, ANEXOS_COMPLETOS), "não informado: é necessário")[0]["nivel"] == "aviso"


def test_anexos_faltando_aparecem_como_aviso():
    itens = checar(SERVICO_COMPLETO, [], [], CONTAGEM_OK, [])
    for trecho in ("Matrícula não anexada", "Nenhuma planta", "Nenhum memorial", "ART/RRT", "Recibo ou demonstrativo"):
        assert por_texto(itens, trecho)[0]["nivel"] == "aviso", trecho


def test_car_sem_sede_e_sem_modulo():
    sem_sede = {"perimetro": {"total": 1, "invalidas": 0, "pontos": 5}}
    itens = checar({**SERVICO_COMPLETO, "modulo_fiscal_ha": None, "codigo_car": None}, [], [], sem_sede, ANEXOS_COMPLETOS)
    assert por_texto(itens, "Sede ou ponto")[0]["nivel"] == "aviso"
    assert por_texto(itens, "Módulo fiscal")[0]["nivel"] == "aviso"
    assert por_texto(itens, "Código do CAR não")[0]["nivel"] == "aviso"


def test_imovel_nao_inscrito_e_so_informacao():
    itens = checar({**SERVICO_COMPLETO, "car_situacao": "nao_inscrito"}, [], [], CONTAGEM_OK, ANEXOS_COMPLETOS)
    assert por_texto(itens, "não inscrito")[0]["nivel"] == "info"


def test_grupos_e_base_legal_em_cada_item():
    itens = checar(SERVICO_COMPLETO, [], [], CONTAGEM_OK, ANEXOS_COMPLETOS)
    assert {i["grupo"] for i in itens} == {"Georreferenciamento", "CAR"}
    assert all(i["base"] for i in itens)


def test_marco_acima_da_precisao_e_aviso_e_ponto_fica_para_o_tecnico():
    vertices = [
        {"indice": 0, "codigo": "M-1", "tipo": "M", "sigma_x": 0.10, "sigma_y": 0.80},
        {"indice": 1, "codigo": "M-2", "tipo": "M", "sigma_x": 0.10, "sigma_y": 0.20},
        {"indice": 2, "codigo": "P-1", "tipo": "P", "sigma_x": 1.00, "sigma_y": 1.00},
    ]
    confrontantes = [{"indice": i} for i in range(3)]
    itens = checar(SERVICO_COMPLETO, vertices, confrontantes, CONTAGEM_OK, ANEXOS_COMPLETOS)
    marcos = por_texto(itens, "Marco(s) com precisão acima")
    assert marcos and marcos[0]["nivel"] == "aviso" and "M-1" in marcos[0]["texto"] and "M-2" not in marcos[0]["texto"]
    assert por_texto(itens, "pontos P e V")[0]["nivel"] == "manual"


def test_marcos_dentro_da_precisao_sao_ok():
    vertices = [{"indice": 0, "codigo": "M-1", "tipo": "M", "sigma_x": 0.3, "sigma_y": 0.4}]
    itens = checar(SERVICO_COMPLETO, vertices, [{"indice": 0}], CONTAGEM_OK, ANEXOS_COMPLETOS)
    assert por_texto(itens, "Marcos dentro da precisão")[0]["nivel"] == "ok"
