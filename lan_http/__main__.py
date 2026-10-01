"""Run with: python -m lan_http --root ./shared --port 8080."""
import argparse
from pathlib import Path
from .server import FileServer


def main():
    parser = argparse.ArgumentParser(description="Educational HTTP/1.1 static file server")
    parser.add_argument("--root", type=Path, required=True, help="Directory of files to share")
    parser.add_argument("--host", default="127.0.0.1", help="Interface; use 0.0.0.0 for LAN (trusted networks only)")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        server = FileServer(args.root, args.host, args.port)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Serving {server.root} at http://{server.address[0]}:{server.address[1]}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close()


if __name__ == "__main__":
    main()
