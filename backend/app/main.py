from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from psycopg.errors import UniqueViolation

from . import repo
from .config import settings
from .db import connect
from .schemas import ArquivoIn, CamadaIn, ClienteIn, OffsetIn, PerimetroIn, ServicoIn, ServicoUpdate

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

@app.post("/clientes", status_code=status.HTTP_201_CREATED)
def criar_cliente(data: ClienteIn):
    with connect() as conn:
        return repo.create_cliente(conn, data)


@app.get("/clientes")
def listar_clientes():
    with connect() as conn:
        return repo.list_clientes(conn)


# ---------- serviços ----------

@app.post("/servicos", status_code=status.HTTP_201_CREATED)
def criar_servico(data: ServicoIn):
    with connect() as conn:
        return repo.create_servico(conn, data)


@app.get("/servicos")
def listar_servicos(cliente_id: int | None = None):
    with connect() as conn:
        return repo.list_servicos(conn, cliente_id)


@app.get("/servicos/{servico_id}")
def obter_servico(servico_id: int):
    with connect() as conn:
        return repo.get_servico(conn, servico_id)


@app.post("/servicos/{servico_id}/revisoes", status_code=status.HTTP_201_CREATED)
def criar_revisao(servico_id: int):
    with connect() as conn:
        return repo.nova_revisao(conn, servico_id)


@app.put("/servicos/{servico_id}/perimetro")
def salvar_perimetro(servico_id: int, data: PerimetroIn):
    with connect() as conn:
        resumo = repo.put_perimetro(conn, servico_id, data.ring, data.vertices, data.confrontantes)
        return {"servico_id": servico_id, **resumo}


@app.put("/servicos/{servico_id}/camadas/{categoria}")
def salvar_camada(servico_id: int, categoria: str, data: CamadaIn):
    with connect() as conn:
        return {"servico_id": servico_id, **repo.put_camada(conn, servico_id, categoria, data.features)}


@app.post("/servicos/{servico_id}/offset")
def criar_offset(servico_id: int, data: OffsetIn):
    with connect() as conn:
        return {"servico_id": servico_id, **repo.offset(conn, servico_id, data.categoria, data.indice, data.distancia_m)}


@app.put("/servicos/{servico_id}")
def atualizar_servico(servico_id: int, data: ServicoUpdate):
    with connect() as conn:
        return repo.update_servico(conn, servico_id, data)


@app.get("/servicos/{servico_id}/arquivos")
def listar_arquivos(servico_id: int):
    with connect() as conn:
        return repo.listar_arquivos(conn, servico_id)


@app.post("/servicos/{servico_id}/arquivos", status_code=status.HTTP_201_CREATED)
def enviar_arquivo(servico_id: int, data: ArquivoIn):
    with connect() as conn:
        return repo.salvar_arquivo(conn, servico_id, data, settings.storage_dir)


@app.get("/arquivos/{arquivo_id}")
def baixar_arquivo(arquivo_id: int):
    with connect() as conn:
        nome, caminho = repo.caminho_arquivo(conn, arquivo_id, settings.storage_dir)
    return FileResponse(caminho, media_type="application/pdf", filename=nome)


@app.delete("/servicos/{servico_id}/perimetro")
def remover_perimetro(servico_id: int):
    with connect() as conn:
        return repo.delete_perimetro(conn, servico_id)


# Interface na mesma origem da API: sem CORS, basta abrir http://127.0.0.1:8000/
@app.get("/", include_in_schema=False)
def interface():
    return FileResponse(ROOT / "index.html")


app.mount("/css", StaticFiles(directory=ROOT / "css"), name="css")
app.mount("/js", StaticFiles(directory=ROOT / "js"), name="js")
