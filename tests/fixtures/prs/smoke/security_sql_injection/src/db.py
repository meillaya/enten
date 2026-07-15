from typing import Any


def get_conn() -> Any:
    raise NotImplementedError







def search_users(name):
    conn = get_conn()
    query = "SELECT * FROM users WHERE name = ?"
    return conn.execute(query, (name,))
