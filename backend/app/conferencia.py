"""Conferência do projeto: o que o GeoWork confere sozinho e o que fica para o técnico.

Cada item sai com um nível: 'ok', 'aviso' (falta algo para a entrega), 'erro' (está errado) ou 'manual'
(exige a norma ou a assinatura do técnico, o sistema não confere). A base legal vem junto de cada grupo.
Nenhum dado pessoal de confrontante é devolvido: só o que falta, não quem é.
"""

import re

BASE_GEO = "Lei 10.267/2001; Decreto 4.449/2002; norma técnica do INCRA para georreferenciamento de imóveis rurais"
BASE_CAR = "Lei 12.651/2012 (Código Florestal) e Instrução Normativa MMA 2/2014 (CAR)"


def checar(servico: dict, vertices: list[dict], confrontantes: list[dict],
           contagem: dict[str, dict], anexos: list[dict]) -> list[dict]:
    itens: list[dict] = []

    def add(nivel: str, texto: str, base: str | None = None) -> None:
        itens.append({"grupo": grupo, "nivel": nivel, "texto": texto, "base": base})

    # ---------- georreferenciamento ----------
    grupo = "Georreferenciamento"
    per = contagem.get("perimetro", {"total": 0, "invalidas": 0, "pontos": 0})
    if not per["total"]:
        add("erro", "Perímetro não desenhado nem importado.", BASE_GEO)
    else:
        n_vertices = max(per["pontos"] - 1, 0)
        if n_vertices < 3:
            add("erro", f"Apenas {n_vertices} vértice(s) no perímetro; o mínimo é 3.", BASE_GEO)
        else:
            add("ok", f"Perímetro com {n_vertices} vértices.", BASE_GEO)
        if per["invalidas"]:
            add("erro", "O perímetro tem geometria inválida (auto-interseção ou anel aberto).", BASE_GEO)
        else:
            add("ok", "Perímetro válido e fechado.", BASE_GEO)

    if vertices:
        com_confrontante = {c["indice"] for c in confrontantes}
        sem = [v for v in vertices if v["indice"] not in com_confrontante]
        if sem:
            add("aviso", f"{len(sem)} vértice(s) sem confrontante informado.", BASE_GEO)
        else:
            add("ok", "Todos os vértices têm confrontante informado.", BASE_GEO)
        sem_precisao = [v for v in vertices if v.get("sigma_x") is None or v.get("sigma_y") is None]
        if sem_precisao:
            add("aviso", f"{len(sem_precisao)} vértice(s) sem precisão (sigma) informada.", BASE_GEO)
        else:
            add("ok", "Precisão informada em todos os vértices.", BASE_GEO)
    else:
        add("aviso", "Vértices e confrontantes do SIGEF ainda não importados.", BASE_GEO)

    sncr = re.sub(r"\D", "", servico.get("codigo_sncr") or "")
    if not servico.get("codigo_sncr"):
        add("aviso", "Código SNCR (INCRA) não informado: é necessário para a certificação.", BASE_GEO)
    elif len(sncr) != 13:
        add("erro", f"Código SNCR com {len(sncr)} dígitos; o certo tem 13.", BASE_GEO)
    else:
        add("ok", "Código SNCR com 13 dígitos.", BASE_GEO)

    if not servico.get("matricula"):
        add("aviso", "Número da matrícula não informado.", BASE_GEO)
    if servico.get("area_documental_ha") is None:
        add("aviso", "Área da matrícula não informada: a comparação com a área desenhada fica de fora.", BASE_GEO)

    categorias = {a["categoria"] for a in anexos}
    pastas = {a["pasta"] for a in anexos}
    if "matricula" not in categorias:
        add("aviso", "Matrícula não anexada.", BASE_GEO)
    if "plantas" not in pastas:
        add("aviso", "Nenhuma planta anexada (pasta Plantas).", BASE_GEO)
    if "memoriais" not in pastas:
        add("aviso", "Nenhum memorial anexado (pasta Memoriais).", BASE_GEO)
    if "art" not in categorias:
        add("aviso", "ART/RRT do responsável técnico não anexada.", BASE_GEO)

    add("manual", "Memorial e planta assinados pelo responsável técnico.", BASE_GEO)
    add("manual", "Precisão dos vértices conferida conforme a norma técnica do INCRA.", BASE_GEO)
    add("manual", "Certificação no SIGEF e notificação dos confrontantes.", BASE_GEO)

    # ---------- CAR ----------
    grupo = "CAR"
    if not servico.get("codigo_car"):
        add("aviso", "Código do CAR não informado.", BASE_CAR)
    else:
        add("ok", "Código do CAR informado.", BASE_CAR)
    if "recibo_car" not in categorias:
        add("aviso", "Recibo ou demonstrativo do CAR não anexado.", BASE_CAR)
    if contagem.get("sede", {}).get("total", 0):
        add("ok", "Sede ou ponto de referência marcado.", BASE_CAR)
    else:
        add("aviso", "Sede ou ponto de referência não marcado (obrigatório no CAR).", BASE_CAR)
    if servico.get("modulo_fiscal_ha") is None:
        add("aviso", "Módulo fiscal do município não informado.", BASE_CAR)
    if servico.get("car_situacao") == "nao_inscrito":
        add("info", "Imóvel ainda não inscrito no CAR.", BASE_CAR)

    return itens
