"""Launch the local observer independently of any Codex production session."""
import argparse
import os
from pathlib import Path
from .server.http import create_server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', nargs='+', required=True, help='Task roots or individual run directories')
    parser.add_argument('--port', type=int, default=8790)
    parser.add_argument('--enable-chat',action='store_true',help='Enable the durable local revision worker')
    parser.add_argument('--enable-editor',action='store_true',help='Enable manual canvas editing without an SDK')
    parser.add_argument('--enable-motion',action='store_true',help='Enable mixed media upload and serial native-frame motion rendering')
    parser.add_argument('--media-root',help='Live upload and durable queue directory outside the source repository')
    parser.add_argument('--cutout-model',help='BiRefNet FP32 ONNX model for newly uploaded tasks')
    parser.add_argument('--chat-state',help='SQLite queue path outside the source repository')
    parser.add_argument('--sdk-path',help='Installed @openai/codex-sdk/dist/index.js')
    parser.add_argument('--codex-path',help='Codex CLI executable')
    args = parser.parse_args()
    if args.sdk_path:os.environ['COLLAGE_CODEX_SDK']=str(Path(args.sdk_path).resolve())
    if args.codex_path:os.environ['COLLAGE_CODEX_CLI']=str(Path(args.codex_path).resolve())
    chat_path=(args.chat_state or str(Path(args.runs[0])/'.workbench/chat.sqlite')) if args.enable_chat else None
    server = create_server(args.runs, args.port,chat_path=chat_path,enable_editor=args.enable_editor,
                           enable_motion=args.enable_motion,media_root=args.media_root,cutout_model=args.cutout_model)
    print(f'Collage workbench: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
