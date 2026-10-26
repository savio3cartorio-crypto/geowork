"""Pacote do projeto: um .zip com os anexos em suas pastas e os dados gerados pelo GeoWork.

Estrutura: plantas/, memoriais/, planilhas/, imagens/, relatorios/ e documentos/ (anexos, na pasta escolhida),
dados/ (GeoPackage e pacote do SICAR, gerados do desenho salvo) e LEIA-ME.txt com o índice.
"""

import io
import zipfile
from datetime import date
from pathlib import Path

from psycopg import Connection

from . import gpkg_export, repo, sicar_export

NOMES_PASTAS = {
    "plantas": "Plantas", "memoriais": "Memoriais", "planilhas": "Planilhas",
    "imagens": "Imagens", "relatorios": "Relatórios", "documentos": "Documentos",
}


def _nome_livre(pasta: str, nome: str, usados: set[str]) -> str:
    """Dois anexos podem ter o mesmo nome na mesma pasta: o segundo ganha um número antes da extensão."""
    base = Path(nome)
    candidato, n = f"{pasta}/{nome}", 2
    while candidato.lower() in usados:
        candidato = f"{pasta}/{base.stem} ({n}){base.suffix}"
        n += 1
    usados.add(candidato.lower())
    return candidato


def gerar(conn: Connection, servico_id: int, storage_dir: Path) -> tuple[str, bytes]:
    servico = repo.get_servico(conn, servico_id)
    repo._require_servico(conn, servico_id)
    anexos = conn.execute(
        "SELECT nome_arquivo, pasta, caminho FROM arquivos_dados WHERE servico_id = %s ORDER BY enviado_em, id",
        (servico_id,),
    ).fetchall()
    leia = [
        "PACOTE DO PROJETO - GeoWork",
        f"Imóvel: {servico['nome_imovel']} (Rev {servico['revisao']:02d})",
    ]
    if servico.get("codigo_car"):
        leia.append(f"Código do CAR: {servico['codigo_car']}")
    leia += [f"Gerado em: {date.today().strftime('%d/%m/%Y')}", "", "Conteúdo:"]

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as pacote:
        usados: set[str] = set()
        for pasta, rotulo in NOMES_PASTAS.items():
            do_grupo = [a for a in anexos if a["pasta"] == pasta]
            if not do_grupo:
                continue
            leia.append(f"  {pasta}/ ({rotulo}, {len(do_grupo)} arquivo(s))")
            for anexo in do_grupo:
                nome = _nome_livre(pasta, anexo["nome_arquivo"], usados)
                pacote.write(storage_dir / anexo["caminho"], nome)
                leia.append(f"    {nome}")

        # dados gerados do desenho salvo; sem desenho, a pasta não é criada
        dados: list[str] = []
        try:
            camadas = repo.camadas_para_gpkg(conn, servico_id)
            pacote.writestr(f"dados/servico-{servico_id}.gpkg", gpkg_export.gerar(camadas))
            dados.append(f"  dados/servico-{servico_id}.gpkg (GeoPackage: perímetro, vértices e camadas do CAR)")
        except ValueError:
            pass
        try:
            servico_sicar, camadas_sicar = repo.camadas_para_sicar(conn, servico_id, sicar_export.CATEGORIAS)
            pacote.writestr("dados/pacote-sicar.zip", sicar_export.gerar(servico_sicar, camadas_sicar))
            dados.append("  dados/pacote-sicar.zip (shapefiles para a etapa Geo do SICAR)")
        except ValueError:
            pass
        if dados:
            leia += ["  dados/ (gerado do desenho salvo)", *dados]

        if not any(a["pasta"] == "memoriais" for a in anexos):
            leia += [
                "",
                "Sem memorial descritivo no projeto. Gere-o na aba 4 (ENTREGAS), em",
                "'Georreferenciamento completo', e baixe o pacote de novo.",
            ]
        pacote.writestr("LEIA-ME.txt", "\r\n".join(leia) + "\r\n")
    return f"projeto-{servico_id}.zip", saida.getvalue()
