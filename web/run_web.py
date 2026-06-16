"""Start the FastAPI web UI with Uvicorn.

Run:
    python -m web.run_web
    python -m web.run_web --port 8085
"""
import argparse
import uvicorn

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--host', default='0.0.0.0')
    args = parser.parse_args()
    uvicorn.run("web.app:app", host=args.host, port=args.port, reload=False)
