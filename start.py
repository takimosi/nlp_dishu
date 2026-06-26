import subprocess
import time
import webbrowser
import os
import signal
import sys
import socket

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
processes = []

# 获取当前 Python 解释器路径（虚拟环境）
PYTHON_EXE = sys.executable
print(f"🐍 使用 Python: {PYTHON_EXE}")

BIND_HOST = "127.0.0.1"
PUBLIC_HOST = "localhost"


def is_port_open(port, host=BIND_HOST):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex((host, port)) == 0


def wait_for_port(port, host=BIND_HOST, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if is_port_open(port, host):
            return True
        time.sleep(0.2)
    return False


def start_server(name, cmd, cwd, port, env=None):
    try:
        print(f"🚀 启动 {name}...")
        if is_port_open(port):
            print(f"⚠️ {name} 未重复启动：端口 {port} 已被占用，请先关闭旧服务")
            return "existing"

        if sys.platform == "win32":
            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env)

        time.sleep(0.3)
        if proc.poll() is not None:
            print(f"❌ {name} 启动失败：进程已退出，返回码 {proc.returncode}")
            return "failed"

        if not wait_for_port(port):
            proc.terminate()
            print(f"❌ {name} 启动失败：端口 {port} 没有监听")
            return "failed"

        processes.append(proc)
        print(f"✅ {name} 已启动 (端口 {port})")
        return "started"
    except Exception as e:
        print(f"❌ {name} 启动失败: {e}")
        return "failed"


def stop_all():
    print("\n🛑 正在停止所有服务...")
    for proc in processes:
        if proc.poll() is None:
            if sys.platform == "win32":
                proc.terminate()
            else:
                os.kill(proc.pid, signal.SIGTERM)
    print("✅ 所有服务已停止")
    sys.exit(0)


def main():
    print("=" * 60)
    print("🎮 地书世界 · 统一启动器")
    print(f"🐍 Python: {PYTHON_EXE}")
    print("=" * 60)
    service_statuses = []

    # 1. 启动 HTTP 服务器 (端口 8080)
    http_cmd = [PYTHON_EXE, "-m", "http.server", "8080", "--bind", BIND_HOST]
    service_statuses.append(start_server("HTTP服务器 (主城/咖啡馆/游戏)", http_cmd, BASE_DIR, 8080))

    # 2. 启动日记服务 (端口 8000)
    diary_dir = os.path.join(BASE_DIR, "diary")
    if os.path.exists(diary_dir):
        diary_cmd = [PYTHON_EXE, "dishu_diary_apartment_server.py"]
        diary_env = os.environ.copy()
        diary_env["DISHU_APARTMENT_PORT"] = "8000"
        service_statuses.append(start_server("日记服务", diary_cmd, diary_dir, 8000, env=diary_env))
    else:
        print("日记服务目录不存在，跳过")

    # 3. 启动符号接龙服务 (端口 5001)
    chain_dir = os.path.join(BASE_DIR, "game")
    if os.path.exists(chain_dir):
        chain_cmd = [PYTHON_EXE, "chain_server.py"]
        service_statuses.append(start_server("符号接龙服务", chain_cmd, chain_dir, 5001))
    else:
        print("符号接龙服务目录不存在，跳过")

    print("\n" + "=" * 60)
    if all(status in {"started", "existing"} for status in service_statuses):
        print("服务检查完成，可按下面地址访问")
    else:
        print("部分服务启动失败，请查看上面的错误信息")
    print("=" * 60)
    print("访问地址：")
    print(f"地书世界 (主城): http://{PUBLIC_HOST}:8080")
    print(f"咖啡馆: http://{PUBLIC_HOST}:8080/Boook/book.html")
    print(f"邮局(日记): http://{PUBLIC_HOST}:8000/dishu_diary_apartment.html")
    print(f"电玩城(游戏): http://{PUBLIC_HOST}:8080/game/templates/index.html")
    print(f"符号接龙: http://{PUBLIC_HOST}:5001")
    print("=" * 60)
    print("按 Ctrl+C 停止所有服务")
    print("=" * 60)

    webbrowser.open(f"http://{PUBLIC_HOST}:8080")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        stop_all()


if __name__ == "__main__":
    main()
