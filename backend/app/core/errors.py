from fastapi import HTTPException


def bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": code, "message": message})


def not_found(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"code": code, "message": message})


def conflict(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"code": code, "message": message})
