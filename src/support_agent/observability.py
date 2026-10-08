import json
import logging
import re
import time
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastapi import Request, Response
from fastapi.responses import JSONResponse

logger = logging.getLogger("support_agent")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def configure_file_logging(folder: str) -> None:
    path = Path(folder).resolve()
    path.mkdir(parents=True, exist_ok=True)
    filename = path / "service.log"
    if any(isinstance(h, RotatingFileHandler) and Path(h.baseFilename) == filename
           for h in logger.handlers):
        return
    handler = RotatingFileHandler(filename, maxBytes=5 * 1024 * 1024,
                                  backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


async def request_observability(request: Request, call_next) -> Response:
    """记录每次 HTTP 请求的编号、路径、状态码和耗时。"""
    supplied = request.headers.get("x-request-id", "")
    request_id = supplied if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", supplied) else str(uuid.uuid4())
    started = time.perf_counter()
    status_code = 500
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception:
        logger.exception("Request failed: %s %s [%s]", request.method,
                         request.url.path, request_id)
        response = JSONResponse(status_code=500, content={
            "detail": "服务暂时异常，请稍后重试；记录编号：" + request_id,
            "request_id": request_id,
        })
        return response
    finally:
        # 放在 finally 中可确保接口即使抛出异常，也会留下观测日志。
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        logger.info(
            json.dumps(
                {
                    "event": "http_request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": status_code,
                    "duration_ms": duration_ms,
                },
                ensure_ascii=False,
            )
        )
        if "response" in locals():
            response.headers["x-request-id"] = request_id
            if request.url.path.startswith("/admin/"):
                response.headers["Cache-Control"] = "no-store"
