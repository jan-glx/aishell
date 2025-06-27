import json
import logging
from datetime import datetime
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import Message

logger = logging.getLogger("api_logger")
logger.setLevel(logging.INFO)
handler = logging.FileHandler("api_log.jsonl")
logger.addHandler(handler)

class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Read and store body for reuse
        body = await request.body()
        input_text = body.decode("utf-8", errors="replace")

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}
        request._receive = receive

        # Get response
        response = await call_next(request)

        # Clone response content
        resp_body = b""
        async for chunk in response.body_iterator:
            resp_body += chunk

        # Construct new response with captured body
        new_response = Response(
            content=resp_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type
        )

        output_text = resp_body.decode("utf-8", errors="replace")

        # Write log entry
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "method": request.method,
            "path": request.url.path,
            "client_ip": request.client.host,
            "status_code": response.status_code,
            "input": input_text,
            "output": output_text,
        }
        logger.info(json.dumps(log_entry))

        return new_response

