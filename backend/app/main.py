from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg.errors import UniqueViolation

from . import consultas_publicas, docx_export, gpkg_export, repo, sicar_export
from .auth import (
    COOKIE, autenticar, bloqueado, criar_sessao, limpar_falhas, normalizar_email,
    registrar_falha, revogar_sessao, sessao_valida, usuario_atual,
)
from .config import settings
from .db import connect
from .schemas import (
    ArquivoIn, CamadaIn, CamadasIn, ClienteIn, CurvasIn, LoginIn, MemorialDocxIn, OffsetIn, PendenciaIn,
    PendenciaUpdate, PerimetroIn, ServicoIn, ServicoUpdate, SituacaoCarIn,
)

# Raiz do projeto: onde ficam index.html, css/ e js/ (servidos pela própria API)
ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with connect() as conn:
        repo.ensure_default_empresa(conn)
    yield


app = FastAPI(
    title="GeoWork API",
    version="0.1.0",
    description="Backend de georreferenciamento (CAR, SIGEF, memorial) com PostgreSQL + PostGIS.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rotas de dados: exigem sessão válida (cookie gerado pelo login)
api = APIRouter(dependencies=[Depends(usuario_atual)])


@app.exception_handler(ValueError)
async def _entrada_invalida(_request, exc: ValueError):
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(LookupError)
async def _nao_encontrado(_request, exc: LookupError):
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(UniqueViolation)
async def _conflito(_request, exc: UniqueViolation):
    return JSONResponse(status_code=409, content={"detail": "conflito: código, confrontante ou revisão já existe"})


@app.get("/health")
def health():
    """Confere a conexão e a instalação do PostGIS."""
    with connect() as conn:
        versao = conn.execute("SELECT PostGIS_Version() AS v").fetchone()["v"]
    return {"status": "ok", "postgis": versao}


# ---------- clientes ----------

@api.post("/clientes", status_code=status.HTTP_201_CREATED)
def criar_cliente(data: ClienteIn):
    with connect() as conn:
        return repo.create_cliente(conn, data)


@api.put("/clientes/{cliente_id}")
def atualizar_cliente(cliente_id: int, data: ClienteIn):
    with connect() as conn:
        return repo.update_cliente(conn, cliente_id, data)


@api.get("/clientes")
def listar_clientes():
    with connect() as conn:
        return repo.list_clientes(conn)


# ---------- serviços ----------

@api.post("/servicos", status_code=status.HTTP_201_CREATED)
def criar_servico(data: ServicoIn):
    with connect() as conn:
        return repo.create_servico(conn, data)


@api.get("/servicos")
def listar_servicos(cliente_id: int | None = None):
    with connect() as conn:
        return repo.list_servicos(conn, cliente_id)


@api.get("/buscar")
def buscar(q: str = Query(min_length=2, max_length=100)):
    with connect() as conn:
        return repo.buscar_servicos(conn, q)


@api.get("/servicos/{servico_id}")
def obter_servico(servico_id: int):
    with connect() as conn:
        return repo.get_servico(conn, servico_id)


@api.post("/servicos/{servico_id}/revisoes", status_code=status.HTTP_201_CREATED)
def criar_revisao(servico_id: int):
    with connect() as conn:
        return repo.nova_revisao(conn, servico_id)


@api.put("/servicos/{servico_id}/perimetro")
def salvar_perimetro(servico_id: int, data: PerimetroIn):
    with connect() as conn:
        resumo = repo.put_perimetro(conn, servico_id, data.ring, data.vertices, data.confrontantes)
        return {"servico_id": servico_id, **resumo}


@api.put("/servicos/{servico_id}/camadas")
def salvar_camadas(servico_id: int, data: CamadasIn):
    with connect() as conn:
        return {"servico_id": servico_id, **repo.put_camadas(conn, servico_id, data.camadas)}


@api.put("/servicos/{servico_id}/camadas/{categoria}")
def salvar_camada(servico_id: int, categoria: str, data: CamadaIn):
    with connect() as conn:
        return {"servico_id": servico_id, **repo.put_camada(conn, servico_id, categoria, data.features)}


@api.put("/servicos/{servico_id}/curvas")
def salvar_curvas(servico_id: int, data: CurvasIn):
    with connect() as conn:
        return {"servico_id": servico_id, **repo.put_curvas(conn, servico_id, data)}


@api.delete("/servicos/{servico_id}/curvas")
def remover_curvas(servico_id: int):
    with connect() as conn:
        return repo.apagar_curvas(conn, servico_id)


@api.post("/servicos/{servico_id}/offset")
def criar_offset(servico_id: int, data: OffsetIn):
    with connect() as conn:
        return {"servico_id": servico_id, **repo.offset(conn, servico_id, data.categoria, data.indice, data.distancia_m)}


@api.put("/servicos/{servico_id}")
def atualizar_servico(servico_id: int, data: ServicoUpdate):
    with connect() as conn:
        return repo.update_servico(conn, servico_id, data)


@api.get("/servicos/{servico_id}/arquivos")
def listar_arquivos(servico_id: int):
    with connect() as conn:
        return repo.listar_arquivos(conn, servico_id)


@api.post("/servicos/{servico_id}/arquivos", status_code=status.HTTP_201_CREATED)
def enviar_arquivo(servico_id: int, data: ArquivoIn):
    with connect() as conn:
        return repo.salvar_arquivo(conn, servico_id, data, settings.storage_dir)


@api.get("/arquivos/{arquivo_id}")
def baixar_arquivo(arquivo_id: int):
    with connect() as conn:
        nome, caminho = repo.caminho_arquivo(conn, arquivo_id, settings.storage_dir)
    return FileResponse(caminho, media_type="application/pdf", filename=nome)


@api.delete("/servicos/{servico_id}/perimetro")
def remover_perimetro(servico_id: int):
    with connect() as conn:
        return repo.delete_perimetro(conn, servico_id)


# Interface na mesma origem da API: sem CORS, basta abrir http://127.0.0.1:8000/
@api.get("/servicos/{servico_id}/exportar.gpkg")
def exportar_gpkg(servico_id: int):
    with connect() as conn:
        camadas = repo.camadas_para_gpkg(conn, servico_id)
    conteudo = gpkg_export.gerar(camadas)
    return Response(
        content=conteudo,
        media_type="application/geopackage+sqlite3",
        headers={"Content-Disposition": f'attachment; filename="servico-{servico_id}.gpkg"'},
    )


# Acompanhamento do CAR depois da inscrição: situação, histórico, prazo e pendências
@api.get("/servicos/{servico_id}/car")
def acompanhamento_car(servico_id: int):
    with connect() as conn:
        return repo.get_acompanhamento(conn, servico_id)


@api.put("/servicos/{servico_id}/car/situacao")
def registrar_situacao_car(servico_id: int, data: SituacaoCarIn):
    with connect() as conn:
        return repo.registrar_situacao(conn, servico_id, data)


@api.post("/servicos/{servico_id}/car/pendencias", status_code=201)
def criar_pendencia(servico_id: int, data: PendenciaIn):
    with connect() as conn:
        return repo.criar_pendencia(conn, servico_id, data)


@api.put("/pendencias/{pendencia_id}")
def atualizar_pendencia(pendencia_id: int, data: PendenciaUpdate):
    with connect() as conn:
        return repo.atualizar_pendencia(conn, pendencia_id, data)


@api.delete("/pendencias/{pendencia_id}")
def remover_pendencia(pendencia_id: int):
    with connect() as conn:
        return repo.remover_pendencia(conn, pendencia_id)


# Sobreposição do perímetro com bases públicas oficiais (SICAR, SIGEF, TI, UC, embargos, ANM)
@api.get("/servicos/{servico_id}/sobreposicoes")
def sobreposicoes(servico_id: int):
    with connect() as conn:
        servico = repo.get_servico(conn, servico_id)
        return consultas_publicas.consultar(conn, servico)


# Prazos de complementação vencidos ou vencendo nos próximos `dias` (alerta da tela inicial)
@api.get("/car/prazos")
def prazos_car(dias: int = Query(default=30, ge=0, le=365)):
    with connect() as conn:
        return repo.prazos_car(conn, dias)


# Um shapefile por camada do CAR, cada um no seu .zip, prontos para a etapa Geo do SICAR
@api.get("/servicos/{servico_id}/exportar-sicar.zip")
def exportar_sicar(servico_id: int):
    with connect() as conn:
        servico, camadas = repo.camadas_para_sicar(conn, servico_id, sicar_export.CATEGORIAS)
    conteudo = sicar_export.gerar(servico, camadas)
    return Response(
        content=conteudo,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="sicar-servico-{servico_id}.zip"'},
    )


@api.post("/memorial.docx")
def memorial_docx(data: MemorialDocxIn):
    conteudo = docx_export.gerar_memorial(data.titulo, data.linhas, data.formato)
    return Response(
        content=conteudo,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="memorial.docx"'},
    )


app.include_router(api)


# ---------- login ----------

@app.get("/login", include_in_schema=False)
def pagina_login(request: Request):
    if sessao_valida(request):
        return RedirectResponse("/", status_code=303)
    return FileResponse(ROOT / "login.html", headers={"Cache-Control": "no-store"})


@app.post("/auth/login")
def login(data: LoginIn, response: Response):
    email = normalizar_email(data.email)
    if bloqueado(email):
        raise HTTPException(status_code=429, detail="Muitas tentativas. Aguarde 15 minutos e tente de novo.")
    with connect() as conn:
        usuario = autenticar(conn, email, data.senha)
        if usuario is None:
            registrar_falha(email)
            raise HTTPException(status_code=401, detail="E-mail ou senha inválidos")
        limpar_falhas(email)
        token = criar_sessao(conn, usuario["id"])
    response.set_cookie(
        COOKIE,
        token,
        max_age=settings.sessao_horas * 3600,
        httponly=True,
        secure=settings.cookie_seguro,
        samesite="strict",
        path="/",
    )
    return {"email": usuario["email"], "papel": usuario["papel"]}


@app.post("/auth/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(COOKIE)
    if token:
        with connect() as conn:
            revogar_sessao(conn, token)
    response.delete_cookie(COOKIE, path="/", httponly=True, secure=settings.cookie_seguro, samesite="strict")
    return {"ok": True}


@app.get("/auth/eu")
def quem_sou(usuario: dict = Depends(usuario_atual)):
    return {"email": usuario["email"], "papel": usuario["papel"]}


@app.get("/", include_in_schema=False)
def interface(request: Request):
    if not sessao_valida(request):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(ROOT / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/css", StaticFiles(directory=ROOT / "css"), name="css")
app.mount("/js", StaticFiles(directory=ROOT / "js"), name="js")
# Bibliotecas de terceiros em versões fixas (Leaflet, proj4, Turf, shpjs, togeojson), servidas pelo próprio sistema
app.mount("/vendor", StaticFiles(directory=ROOT / "vendor"), name="vendor")
