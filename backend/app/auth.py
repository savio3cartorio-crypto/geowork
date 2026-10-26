"""Login do sistema: senhas com scrypt (biblioteca padrão), sessões guardadas no banco e cookie HTTP-only.

O cookie guarda um token aleatório; o banco guarda só o hash SHA-256 dele. Sair do sistema apaga a sessão
no servidor, então um cookie copiado antes do logout deixa de valer.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import settings
from .db import connect

COOKIE = "gw_sessao"

# Parâmetros do scrypt: custo de memória e tempo adequados para login interativo
_N, _R, _P = 2**14, 8, 1

# Bloqueio por e-mail: depois de tantas falhas na janela, recusa novas tentativas por um tempo
MAX_FALHAS = 5
JANELA_FALHAS_S = 15 * 60
_falhas: dict[str, deque] = defaultdict(deque)


def _b64(dados: bytes) -> str:
    return base64.b64encode(dados).decode()


def hash_senha(senha: str) -> str:
    sal = secrets.token_bytes(16)
    chave = hashlib.scrypt(senha.encode(), salt=sal, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${_b64(sal)}${_b64(chave)}"


def verificar_senha(senha: str, armazenado: str) -> bool:
    try:
        _, n, r, p, sal, chave = armazenado.split("$")
        esperada = base64.b64decode(chave)
        calculada = hashlib.scrypt(
            senha.encode(), salt=base64.b64decode(sal), n=int(n), r=int(r), p=int(p), dklen=len(esperada)
        )
    except ValueError:
        return False
    return hmac.compare_digest(calculada, esperada)


# Hash descartável: usado quando o e-mail não existe, para o tempo de resposta não revelar contas
_HASH_FICTICIO = hash_senha(secrets.token_urlsafe(16))


def normalizar_email(email: str) -> str:
    return email.strip().lower()


def autenticar(conn, email: str, senha: str) -> dict | None:
    usuario = conn.execute(
        "SELECT id, email, papel, senha_hash, ativo FROM usuarios WHERE email = %s", (email,)
    ).fetchone()
    senha_ok = verificar_senha(senha, usuario["senha_hash"] if usuario else _HASH_FICTICIO)
    if usuario is None or not usuario["ativo"] or not senha_ok:
        return None
    return usuario


def bloqueado(email: str) -> bool:
    fila = _falhas[email]
    agora = time.monotonic()
    while fila and agora - fila[0] > JANELA_FALHAS_S:
        fila.popleft()
    return len(fila) >= MAX_FALHAS


def registrar_falha(email: str) -> None:
    if len(_falhas) > 10_000:  # evita crescimento sem limite com e-mails aleatórios
        _falhas.clear()
    _falhas[email].append(time.monotonic())


def limpar_falhas(email: str) -> None:
    _falhas.pop(email, None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def criar_sessao(conn, usuario_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn.execute("DELETE FROM sessoes WHERE expira_em < now()")
    conn.execute(
        "INSERT INTO sessoes (token_hash, usuario_id, expira_em) VALUES (%s, %s, now() + make_interval(hours => %s))",
        (_hash_token(token), usuario_id, settings.sessao_horas),
    )
    return token


def revogar_sessao(conn, token: str) -> None:
    conn.execute("DELETE FROM sessoes WHERE token_hash = %s", (_hash_token(token),))


def usuario_da_sessao(conn, token: str) -> dict | None:
    return conn.execute(
        """
        SELECT u.id, u.email, u.papel
        FROM sessoes s JOIN usuarios u ON u.id = s.usuario_id
        WHERE s.token_hash = %s AND s.expira_em > now() AND u.ativo
        """,
        (_hash_token(token),),
    ).fetchone()


def sessao_valida(request: Request) -> dict | None:
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    with connect() as conn:
        return usuario_da_sessao(conn, token)


def usuario_atual(request: Request) -> dict:
    """Dependência das rotas da API: sem sessão válida, responde 401."""
    usuario = sessao_valida(request)
    if usuario is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return usuario
