"""Administração de usuários pelo terminal do servidor. Não há cadastro pela web.

    .venv/bin/python -m app.usuarios criar voce@exemplo.com --papel master
    .venv/bin/python -m app.usuarios senha voce@exemplo.com

A senha é pedida sem eco e precisa ter pelo menos 12 caracteres.
"""

import argparse
import getpass
import sys

from .auth import hash_senha, normalizar_email
from .db import connect

MIN_SENHA = 12


def _pedir_senha() -> str:
    senha = getpass.getpass("Senha: ")
    if len(senha) < MIN_SENHA:
        sys.exit(f"A senha precisa ter pelo menos {MIN_SENHA} caracteres.")
    if getpass.getpass("Repita a senha: ") != senha:
        sys.exit("As senhas não conferem.")
    return senha


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Gerencia usuários do GeoWork.")
    sub = parser.add_subparsers(dest="comando", required=True)

    criar = sub.add_parser("criar", help="cria um usuário")
    criar.add_argument("email")
    criar.add_argument("--papel", choices=["master", "usuario"], default="usuario")

    trocar = sub.add_parser("senha", help="troca a senha e encerra as sessões do usuário")
    trocar.add_argument("email")

    args = parser.parse_args(argv)
    email = normalizar_email(args.email)
    senha_hash = hash_senha(_pedir_senha())

    with connect() as conn:
        if args.comando == "criar":
            conn.execute(
                "INSERT INTO usuarios (email, senha_hash, papel) VALUES (%s, %s, %s)",
                (email, senha_hash, args.papel),
            )
            print(f"Usuário {email} criado com papel {args.papel}.")
        else:
            usuario = conn.execute("SELECT id FROM usuarios WHERE email = %s", (email,)).fetchone()
            if usuario is None:
                sys.exit("Usuário não encontrado.")
            conn.execute("UPDATE usuarios SET senha_hash = %s WHERE id = %s", (senha_hash, usuario["id"]))
            conn.execute("DELETE FROM sessoes WHERE usuario_id = %s", (usuario["id"],))
            print("Senha alterada. Sessões abertas foram encerradas.")


if __name__ == "__main__":
    main()
