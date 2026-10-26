import re
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Coord = tuple[float, float]  # [lng, lat] em SIRGAS 2000 geográfico

TipoDivisa = Literal["PESSOA", "FAIXA_DOMINIO", "CORREGO_RIO", "ESTRADA", "OUTRO"]
# Camadas que aceitam OFFSET (polígonos e linhas); a sede é ponto e o perímetro tem rota própria
CategoriaLinear = Literal[
    "vegetacao_nativa", "area_consolidada", "pousio", "servidao", "app", "uso_restrito",
    "rl", "rl_averbada", "rl_aprovada", "rl_compensacao", "hidro", "edificacao",
]
CategoriaDesenho = Literal[
    "sede", "vegetacao_nativa", "area_consolidada", "pousio", "servidao", "app", "uso_restrito",
    "rl", "rl_averbada", "rl_aprovada", "rl_compensacao", "hidro", "edificacao",
]

# Número do CAR no SICAR: UF, código IBGE do município (7 dígitos) e 32 caracteres hexadecimais
PADRAO_CAR = re.compile(r"^[A-Z]{2}-\d{7}-[0-9A-F]{32}$")


def normalizar_car(valor: str | None) -> str | None:
    if valor is None or not valor.strip():
        return None
    car = valor.strip().upper()
    if not PADRAO_CAR.match(car):
        raise ValueError("código CAR fora do formato UF-CódigoIBGE-HASH, ex.: SP-3538709-E4A558DDFC5649D0A3524EB344ECC52D")
    return car


UFS = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}


def normalizar_cep(valor: str | None) -> str | None:
    if valor is None or not valor.strip():
        return None
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) != 8:
        raise ValueError("CEP precisa ter 8 dígitos, ex.: 01001-000")
    return f"{digitos[:5]}-{digitos[5:]}"


def normalizar_uf(valor: str | None) -> str | None:
    if valor is None or not valor.strip():
        return None
    uf = valor.strip().upper()
    if uf not in UFS:
        raise ValueError(f"UF inválida: {uf}")
    return uf


class ClienteIn(BaseModel):
    nome: str = Field(min_length=2, max_length=200)
    cpf_cnpj: str = Field(min_length=1, max_length=18)
    email: str | None = None
    telefone: str | None = None
    cep: str | None = None
    logradouro: str | None = Field(default=None, max_length=200)
    numero: str | None = Field(default=None, max_length=20)
    complemento: str | None = Field(default=None, max_length=100)
    bairro: str | None = Field(default=None, max_length=100)
    municipio: str | None = Field(default=None, max_length=100)
    uf: str | None = None

    @field_validator("logradouro", "numero", "complemento", "bairro", "municipio", mode="before")
    @classmethod
    def _texto(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return " ".join(str(v).split()) or None

    @field_validator("cep")
    @classmethod
    def _valida_cep(cls, v: str | None) -> str | None:
        return normalizar_cep(v)

    @field_validator("uf")
    @classmethod
    def _valida_uf(cls, v: str | None) -> str | None:
        return normalizar_uf(v)


class ServicoIn(BaseModel):
    cliente_id: int
    nome_imovel: str = Field(min_length=2, max_length=200)
    tipo: Literal["GEORREFERENCIAMENTO", "CAR", "USUCAPIAO", "RETIFICACAO", "ORCAMENTO"] = "GEORREFERENCIAMENTO"
    codigo_car: str | None = None
    codigo_sncr: str | None = None
    matricula: str | None = None

    @field_validator("codigo_car")
    @classmethod
    def _valida_car(cls, v: str | None) -> str | None:
        return normalizar_car(v)


class CamadaIn(BaseModel):
    """Substitui todas as feições de uma categoria. Polígonos são fechados pelo servidor."""

    features: list[list[Coord]] = Field(default_factory=list)


class CamadasIn(BaseModel):
    """Várias camadas de uma vez: categoria -> feições. Categorias ausentes não mudam."""

    camadas: dict[CategoriaDesenho, list[list[Coord]]]


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
    area_documental_ha: float | None = Field(default=None, gt=0)
    rl_percentual: float | None = Field(default=None, gt=0, le=100)

    @field_validator("codigo_car")
    @classmethod
    def _valida_car(cls, v: str | None) -> str | None:
        return normalizar_car(v)


CategoriaArquivo = Literal["recibo_car", "matricula", "ccir", "itr_cib", "certidao", "art", "insumo", "outro"]


PastaArquivo = Literal["plantas", "memoriais", "planilhas", "imagens", "relatorios", "documentos"]


class ArquivoIn(BaseModel):
    """Upload de um anexo do projeto (PDF, DXF, DOCX, XLSX, JPG ou PNG), enviado em base64 dentro do JSON."""

    nome_arquivo: str = Field(min_length=1, max_length=200)
    categoria: CategoriaArquivo = "recibo_car"
    pasta: PastaArquivo = "documentos"
    conteudo_base64: str = Field(min_length=1, description="conteúdo do arquivo em base64")


class ArquivoUpdate(BaseModel):
    pasta: PastaArquivo


class MemorialDocxIn(BaseModel):
    titulo: str = Field(min_length=1, max_length=200)
    formato: Literal["corrido", "tabular"] = "corrido"
    linhas: list[str] = Field(min_length=1, max_length=5000)


class CurvaIn(BaseModel):
    cota: float
    partes: list[list[Coord]] = Field(min_length=1)  # cada parte é uma linha [lng, lat]


class CurvasIn(BaseModel):
    """Curvas de nível do serviço. Substitui as anteriores."""

    intervalo: float = Field(gt=0, description="equidistância em metros")
    feicoes: list[CurvaIn] = Field(default_factory=list, max_length=500)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    senha: str = Field(min_length=1, max_length=200)


# Situações do CAR no Módulo de Análise do SICAR-SP, mais "não inscrito" para o que ainda está no GeoWork
SituacaoCar = Literal[
    "nao_inscrito", "inscrito", "em_analise", "aguarda_complementacao", "em_alteracao",
    "aprovado", "aprovado_dispensa_rl", "invalido", "cancelado",
]


class SituacaoCarIn(BaseModel):
    """Nova situação do CAR. Em 'aguarda_complementacao' o órgão dá um prazo: data final ou dias a contar
    da data da situação (a notificação). Sem data, vale hoje."""

    situacao: SituacaoCar
    data: date | None = None
    prazo: date | None = None
    prazo_dias: int | None = Field(default=None, gt=0, le=3650)
    observacao: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _valida_prazo(self):
        if self.prazo and self.prazo_dias:
            raise ValueError("informe o prazo como data ou em dias, não os dois")
        if self.situacao == "aguarda_complementacao":
            if not (self.prazo or self.prazo_dias):
                raise ValueError("a situação 'aguarda complementação' exige o prazo dado pelo órgão")
        elif self.prazo or self.prazo_dias:
            raise ValueError("só a situação 'aguarda complementação' tem prazo")
        return self


class PendenciaIn(BaseModel):
    descricao: str = Field(min_length=1, max_length=2000)

    @field_validator("descricao")
    @classmethod
    def _sem_vazio(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("descreva a pendência")
        return v.strip()


class PendenciaUpdate(BaseModel):
    descricao: str | None = Field(default=None, min_length=1, max_length=2000)
    resolvida: bool | None = None
