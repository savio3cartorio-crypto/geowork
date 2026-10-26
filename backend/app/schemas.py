from typing import Literal

from pydantic import BaseModel, Field

Coord = tuple[float, float]  # [lng, lat] em SIRGAS 2000 geográfico

TipoDivisa = Literal["PESSOA", "FAIXA_DOMINIO", "CORREGO_RIO", "ESTRADA", "OUTRO"]
CategoriaLinear = Literal["rl", "app", "hidro", "edificacao"]


class ClienteIn(BaseModel):
    nome: str = Field(min_length=2, max_length=200)
    cpf_cnpj: str = Field(min_length=1, max_length=18)
    email: str | None = None
    telefone: str | None = None


class ServicoIn(BaseModel):
    cliente_id: int
    nome_imovel: str = Field(min_length=2, max_length=200)
    tipo: Literal["GEORREFERENCIAMENTO", "CAR", "USUCAPIAO", "RETIFICACAO", "ORCAMENTO"] = "GEORREFERENCIAMENTO"
    codigo_car: str | None = None
    codigo_sncr: str | None = None
    matricula: str | None = None


class CamadaIn(BaseModel):
    """Substitui todas as feições de uma categoria. Polígonos são fechados pelo servidor."""

    features: list[list[Coord]] = Field(default_factory=list)


class VerticeMeta(BaseModel):
    codigo: str | None = None
    metodo: str | None = None
    altitude: float | None = None
    sigma_x: float | None = None
    sigma_y: float | None = None
    sigma_z: float | None = None


class ConfrontanteIn(BaseModel):
    indice: int = Field(ge=0, description="índice do vértice inicial do trecho")
    tipo_divisa: TipoDivisa = "PESSOA"
    nome: str = Field(min_length=1, max_length=200)
    cpf_cnpj: str | None = None
    matricula: str | None = None
    cns_cartorio: str | None = Field(default=None, max_length=6)
    codigo_incra_sncr: str | None = None
    orgao_faixa: str | None = None


class PerimetroIn(BaseModel):
    ring: list[Coord] = Field(min_length=3)
    vertices: list[VerticeMeta] = Field(default_factory=list)
    confrontantes: list[ConfrontanteIn] = Field(default_factory=list)


class OffsetIn(BaseModel):
    """OFFSET: cria uma paralela da feição. Sinal: positivo expande (polígono) ou vai à esquerda do traçado (linha)."""

    categoria: CategoriaLinear
    indice: int = Field(ge=0)
    distancia_m: float = Field(description="distância em metros; negativo contrai o polígono ou vai à direita")


class ServicoUpdate(BaseModel):
    """Dados cadastrais editáveis do serviço. Campos ausentes são gravados como nulos."""

    matricula: str | None = None
    codigo_car: str | None = None
    codigo_sncr: str | None = None
    modulo_fiscal_ha: float | None = Field(default=None, gt=0)


CategoriaArquivo = Literal["recibo_car", "matricula", "ccir", "itr_cib", "certidao", "insumo", "outro"]


class ArquivoIn(BaseModel):
    """Upload de um PDF do serviço, enviado em base64 dentro do JSON (sem multipart)."""

    nome_arquivo: str = Field(min_length=1, max_length=200)
    categoria: CategoriaArquivo = "recibo_car"
    conteudo_base64: str = Field(min_length=1, description="conteúdo do PDF em base64")
