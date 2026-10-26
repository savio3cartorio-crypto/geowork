from contextlib import contextmanager
from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row

from .config import settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    """Uma conexão por requisição. Sem pool: suficiente para o protótipo.

    A transação é confirmada ao sair do bloco sem erro e desfeita se houver exceção.
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn
