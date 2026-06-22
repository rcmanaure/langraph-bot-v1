from fastapi import FastAPI, Request, Response

_MAX_BODY = 10 * 1024 * 1024  # 10 MB


def add_security_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        return response

    @app.middleware("http")
    async def limit_request_size(request: Request, call_next):
        cl = request.headers.get("content-length")
        if cl and int(cl) > _MAX_BODY:
            return Response("Request too large", status_code=413)
        return await call_next(request)
