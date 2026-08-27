import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("vllm.request_id")

class RequestIdLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request_id = request.headers.get("x-request-id")
        started = time.perf_counter()
        try:
            response = await call_next(request)
            logger.info(
                "vllm request completed request_id=%s method=%s path=%s status_code=%s duration_seconds=%.6f",
                request_id,
                request.method,
                request.url.path,
                response.status_code,
                time.perf_counter() - started,
            )
            return response
        except Exception:
            logger.exception(
                "vllm request failed request_id=%s method=%s path=%s duration_seconds=%.6f",
                request_id,
                request.method,
                request.url.path,
                time.perf_counter() - started,
            )
            raise