from fastapi import HTTPException


def bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": code, "message": message})


def not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


def conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})


def bad_gateway(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=502, detail={"code": code, "message": message})


def service_unavailable(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=503, detail={"code": code, "message": message})
