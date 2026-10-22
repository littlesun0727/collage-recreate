"""Launch the v6 local workbench with a fixed visual workflow."""
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
    parser.add_argument('--enable-create',action='store_true',help='Automatically produce uploaded collages with the v6 VLM workflow (includes uploads)')
    parser.add_argument('--media-root',help='Live upload and durable queue directory outside the source repository')
    parser.add_argument('--cutout-model',help='BiRefNet FP32 ONNX model for newly uploaded tasks')
    parser.add_argument('--chat-state',help='SQLite queue path outside the source repository')
    parser.add_argument('--vlm-env',help='dotenv file containing OPENROUTER_API_KEY')
    parser.add_argument('--vlm-model',default=os.environ.get('COLLAGE_VLM_MODEL'))
    parser.add_argument('--vlm-provider',choices=['openrouter','yibu'])
    parser.add_argument('--yibu-credentials',help='dotenv file containing YIBU_API_KEY (legacy JSON api_keys also accepted); never sent to the browser')
    parser.add_argument('--reveal-env',help='Existing 360 credential file')
    args = parser.parse_args()
    if args.vlm_env:os.environ['COLLAGE_VLM_ENV']=str(Path(args.vlm_env).resolve())
    if args.vlm_model:os.environ['COLLAGE_VLM_MODEL']=args.vlm_model
    if args.vlm_provider:os.environ['COLLAGE_VLM_PROVIDER']=args.vlm_provider
    if args.yibu_credentials:os.environ['COLLAGE_YIBU_CREDENTIALS']=str(Path(args.yibu_credentials).resolve())
    if args.reveal_env:os.environ['COLLAGE_REVEAL_ENV']=str(Path(args.reveal_env).resolve())
    chat_path=(args.chat_state or str(Path(args.runs[0])/'.workbench/chat.sqlite')) if args.enable_chat else None
    server = create_server(args.runs, args.port,chat_path=chat_path,enable_editor=args.enable_editor,
                           enable_motion=args.enable_motion,media_root=args.media_root,cutout_model=args.cutout_model,
                           enable_create=args.enable_create)
    print(f'Collage workbench: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
