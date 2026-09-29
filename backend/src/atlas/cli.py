import argparse
from pathlib import Path
import socket
import sys

import uvicorn

from atlas.app.factory import create_app
from atlas.core.config import AppConfig
from atlas.instructions.scanner import main as scan_main
from atlas.evaluation.runner import main as eval_main


def main():
    parser = argparse.ArgumentParser(prog="atlas")
    parser.add_argument("--workspace", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve")
    serve.add_argument("--port", type=int)
    serve.add_argument("--frontend-dir", type=Path)
    sub.add_parser("scan")
    evaluation = sub.add_parser("eval")
    evaluation.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.command == "eval":
        return eval_main(args.arguments)
    overrides = {"workspace": args.workspace}
    if args.command == "serve":
        overrides.update(port=args.port, frontend_dir=args.frontend_dir)
    config = AppConfig(**{key: value for key, value in overrides.items() if value is not None})
    if args.command == "scan":
        config.state_dir.mkdir(parents=True, exist_ok=True)
        return scan_main(config)
    family = socket.AF_INET6 if config.host == "::1" else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((config.host, config.port))
        print(f"AgentAtlas 前端访问地址: http://{config.host}:{config.port}")
        print("按 Ctrl-C 停止服务")
        sys.stdout.flush()
        server = uvicorn.Server(uvicorn.Config(create_app(config), host=config.host, port=config.port, workers=1))
        server.run(sockets=[listener])
