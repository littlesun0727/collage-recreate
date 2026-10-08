"""Launch the local observer independently of any Codex production session."""
import argparse
from .server.http import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', required=True, help='Task roots or individual run directories')
    parser.add_argument('--port', type=int, default=8790)
    args = parser.parse_args()
    server = create_server(args.runs, args.port)
    print(f'Collage workbench: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
