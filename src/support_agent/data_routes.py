from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from .models import UserAccount
from .services.auth import current_user
from .services.data_check import MAX_BYTES, compare_tables, read_table

router = APIRouter(prefix="/data-check", tags=["文件数据核对"])
User = Annotated[UserAccount | None, Depends(current_user)]


class TableInput(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    data_time: str = Field(default="", max_length=80)
    key_column: str = Field(min_length=1, max_length=100)
    value_column: str = Field(min_length=1, max_length=100)
    unit_column: str = Field(default="", max_length=100)
    gain_column: str = Field(default="", max_length=100)
    rows: list[dict[str, str]] = Field(min_length=1, max_length=2000)


class Comparison(BaseModel):
    left: TableInput
    right: TableInput
    tolerance: str = Field(default="0.01", max_length=40)


@router.post("/read")
async def read(
    user: User, file: Annotated[UploadFile, File()], sheet: Annotated[str, Form()] = ""
) -> dict:
    data = await file.read(MAX_BYTES + 1)
    try:
        result = await run_in_threadpool(read_table, file.filename or "", data, sheet)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"name": (file.filename or "文件")[:255], **result}


@router.post("/compare")
async def compare(body: Comparison, user: User) -> dict:
    for table in (body.left, body.right):
        if any(
            len(row) > 100 or any(len(k) > 100 or len(v) > 1000 for k, v in row.items())
            for row in table.rows
        ):
            raise HTTPException(422, "数据列数或单元格长度超过限制")
    try:
        return await run_in_threadpool(
            compare_tables, body.left.model_dump(), body.right.model_dump(), body.tolerance
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
