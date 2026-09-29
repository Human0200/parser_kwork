import paramiko
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ssh-server")

# Хранилище активных SSH соединений
_connections: dict[str, paramiko.SSHClient] = {}


def _make_key(host: str, port: int, user: str) -> str:
    return f"{user}@{host}:{port}"


@mcp.tool()
def ssh_connect(
    host: str,
    user: str,
    password: str = "",
    key_path: str = "",
    port: int = 22,
) -> str:
    """
    Установить SSH соединение с сервером.
    Используйте password или key_path (путь к приватному ключу).
    Возвращает ключ соединения для дальнейших вызовов.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if key_path:
            client.connect(host, port=port, username=user, key_filename=key_path, timeout=10)
        elif password:
            client.connect(host, port=port, username=user, password=password, timeout=10)
        else:
            # Попытка с ключами из ssh-agent / ~/.ssh/
            client.connect(host, port=port, username=user, timeout=10)

        key = _make_key(host, port, user)
        _connections[key] = client
        return f"✓ Подключено: {key}"
    except Exception as e:
        return f"✗ Ошибка подключения: {e}"


@mcp.tool()
def ssh_run(
    host: str,
    user: str,
    command: str,
    port: int = 22,
) -> str:
    """
    Выполнить команду на уже подключённом SSH сервере.
    Перед использованием вызовите ssh_connect.
    """
    key = _make_key(host, port, user)
    client = _connections.get(key)
    if client is None:
        return f"✗ Нет соединения с {key}. Сначала вызовите ssh_connect."

    try:
        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()

        result = f"[exit {exit_code}]\n"
        if out:
            result += out
        if err:
            result += f"\n[stderr]\n{err}"
        return result.strip()
    except Exception as e:
        return f"✗ Ошибка выполнения: {e}"


@mcp.tool()
def ssh_run_quick(
    host: str,
    user: str,
    command: str,
    password: str = "",
    key_path: str = "",
    port: int = 22,
) -> str:
    """
    Подключиться, выполнить одну команду и отключиться.
    Удобно для разовых вызовов без хранения сессии.
    """
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        if key_path:
            client.connect(host, port=port, username=user, key_filename=key_path, timeout=10)
        elif password:
            client.connect(host, port=port, username=user, password=password, timeout=10)
        else:
            client.connect(host, port=port, username=user, timeout=10)

        stdin, stdout, stderr = client.exec_command(command, timeout=30)
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        exit_code = stdout.channel.recv_exit_status()

        result = f"[exit {exit_code}]\n"
        if out:
            result += out
        if err:
            result += f"\n[stderr]\n{err}"
        return result.strip()
    except Exception as e:
        return f"✗ Ошибка: {e}"
    finally:
        client.close()


@mcp.tool()
def ssh_disconnect(host: str, user: str, port: int = 22) -> str:
    """Закрыть SSH соединение."""
    key = _make_key(host, port, user)
    client = _connections.pop(key, None)
    if client:
        client.close()
        return f"✓ Отключено: {key}"
    return f"Соединение {key} не найдено."


@mcp.tool()
def ssh_list_connections() -> str:
    """Показать список активных SSH соединений."""
    if not _connections:
        return "Нет активных соединений."
    return "\n".join(_connections.keys())


if __name__ == "__main__":
    mcp.run()
