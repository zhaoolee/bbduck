from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    description='BBDuck Server API for image compression, file preview, and batch ZIP download.',
    version='0.1.0',
    docs_url='/docs',
    redoc_url='/redoc',
    openapi_url='/openapi.json',
)
app.include_router(router, prefix=settings.api_prefix)

frontend_dist = Path(settings.frontend_dist)
if frontend_dist.exists():
    assets_dir = frontend_dist / 'assets'
    if assets_dir.exists():
        app.mount('/assets', StaticFiles(directory=assets_dir), name='assets')


def _serve_frontend_static_file(full_path: str):
    candidate = (frontend_dist / full_path).resolve()
    try:
        candidate.relative_to(frontend_dist.resolve())
    except ValueError:
        return None
    if candidate.is_file():
        return FileResponse(candidate)
    return None


@app.get('/', response_model=None)
def spa_index():
    index_file = frontend_dist / 'index.html'
    if index_file.exists():
        return FileResponse(index_file)
    return {'message': 'frontend build not found'}


@app.get('/{full_path:path}', response_model=None)
def spa_fallback(full_path: str):
    if full_path.startswith('api/'):
        return {'message': 'API route not found'}

    static_file_response = _serve_frontend_static_file(full_path)
    if static_file_response is not None:
        return static_file_response

    index_file = frontend_dist / 'index.html'
    if index_file.exists():
        return FileResponse(index_file)
    return {'message': f'Unknown path: {full_path}'}
