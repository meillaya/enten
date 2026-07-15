from typing import Any


def get_conn() -> Any:
    raise NotImplementedError







def search_users(name):
    conn = get_conn()
    query = f"SELECT * FROM users WHERE name = '{name}'"
    return conn.execute(query)
