"""外部复核路由：核对文件下载、更正登记、凭证领取与异常处置办结。

固定路径必须早于通用资源路由注册，否则会被 /api/{module} 抢先匹配。
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services import external_review as review
from app.store import store

router = APIRouter(prefix="/api/review", tags=["外部复核"])


class DownloadPayload(BaseModel):
    revision: str
    experiment_count: int | None = None
    control_count: int | None = None
    planned_ratio: float | None = None
    threshold: float | None = None


class EvidencePayload(BaseModel):
    revision: str
    experiment_code: str
    reviewer: str = ""
    group_counts: dict[str, Any] | None = None
    submitted_numerator: int | None = None
    submitted_denominator: int | None = None
    adjustment: dict[str, Any] | None = None
    note: str = ""


class ClaimPayload(BaseModel):
    anomaly_entry_id: int | None = None
    operator: str = ""


class SettlePayload(BaseModel):
    decision: str
    governor: bool = False
    remark: str = ""
    operator: str = ""


def _task_or_404(entry_id: int) -> dict[str, Any]:
    entry = store.find("sample_ratio", entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"核对记录 {entry_id} 不存在")
    return entry


@router.get("/tasks/{entry_id}")
def task_detail(entry_id: int) -> dict[str, Any]:
    """核对详情：可下载的轮次、已冻结文件与可用于后续处置的版本。"""
    return review.task_detail(_task_or_404(entry_id))


def _file_or_404(file_id: str) -> dict[str, Any]:
    file_row = review.find_file(file_id)
    if file_row is None:
        raise HTTPException(status_code=404, detail=f"复核文件 {file_id} 不存在")
    return file_row


@router.post("/tasks/{entry_id}/downloads")
def create_download(entry_id: int, payload: DownloadPayload) -> dict[str, Any]:
    """按所选核对版次冻结下载文件，重复下载不再改写。"""
    entry = _task_or_404(entry_id)
    return review.create_download(
        entry,
        revision=payload.revision,
        experiment_count=payload.experiment_count,
        control_count=payload.control_count,
        planned_ratio=payload.planned_ratio,
        threshold=payload.threshold,
    )


@router.get("/files/{file_id}")
def file_detail(file_id: str) -> dict[str, Any]:
    return _file_or_404(file_id)


@router.get("/files/{file_id}/downloads")
def download_file(file_id: str) -> StreamingResponse:
    """下载冻结文件；内容在生成时固化，后续计划调整不会改写。"""
    file_row = _file_or_404(file_id)
    content = review._file_or_content(file_row)
    filename = f"{file_row.get('文件编号')}.csv"
    return StreamingResponse(
        iter([content]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/files/{file_id}/evidence")
def submit_evidence(file_id: str, payload: EvidencePayload) -> dict[str, Any]:
    """登记外部复核更正说明：校验实验、版次与两组计数，错误时保留原始行。"""
    file_row = _file_or_404(file_id)
    return review.submit_evidence(
        file_row,
        revision=payload.revision,
        experiment_code=payload.experiment_code,
        reviewer=payload.reviewer,
        group_counts=payload.group_counts,
        submitted_numerator=payload.submitted_numerator,
        submitted_denominator=payload.submitted_denominator,
        adjustment=payload.adjustment,
        note=payload.note,
    )


@router.get("/files/{file_id}/audit")
def file_audit(file_id: str) -> dict[str, Any]:
    """复核文件关联实验、版次、证据、签收与处置的整体留档。"""
    file_row = _file_or_404(file_id)
    evidence = [r for r in store.rows(review.EVIDENCE) if r.get("文件编号") == file_id]
    signed = [r for r in store.rows(review.SIGNED) if r.get("文件编号") == file_id]
    tickets = [r for r in store.rows(review.TICKETS) if r.get("文件编号") == file_id]
    errors = [r for r in store.rows(review.ERRORS) if r.get("文件编号") == file_id]
    return {
        "ok": True,
        "message": f"文件 {file_id} 关联 {len(evidence)} 条证据、{len(tickets)} 张凭证",
        "entry": file_row,
        "evidence": evidence,
        "signed": signed,
        "tickets": tickets,
        "errors": errors,
    }


@router.get("/evidence/{evidence_id}")
def evidence_detail(evidence_id: str) -> dict[str, Any]:
    row = review.find_evidence(evidence_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"复核证据 {evidence_id} 不存在")
    return {"ok": True, "message": "复核证据已登记", "entry": row}


@router.get("/evidence/{evidence_id}/signed")
def evidence_signed(evidence_id: str) -> dict[str, Any]:
    """读取独立留档的原签收副本。"""
    return review.signed_copy(evidence_id)


@router.post("/evidence/{evidence_id}/claim")
def claim(evidence_id: str, payload: ClaimPayload) -> dict[str, Any]:
    """领取处置凭证：锁定有效证据，形成可从异常检测页办理的凭证。"""
    return review.claim_ticket(
        evidence_id,
        anomaly_entry_id=payload.anomaly_entry_id,
        operator=payload.operator,
    )


@router.get("/anomalies/{entry_id}")
def anomaly_material(entry_id: int) -> dict[str, Any]:
    """异常检测页打开某一告警：核对文件、有效证据与已登记凭证。"""
    entry = store.find("anomaly_metric", entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"异常事件 {entry_id} 不存在")
    return review.anomaly_detail(entry)


@router.get("/tickets")
def list_tickets(exp_code: str | None = None, status: str | None = None) -> dict[str, Any]:
    rows = list(store.rows(review.TICKETS))
    if exp_code:
        rows = [
            r for r in rows
            if exp_code in (str(r.get("实验编码")), str(r.get("关联实验编码")))
        ]
    if status:
        rows = [r for r in rows if r.get("status") == status]
    return {"ok": True, "items": rows, "total": len(rows)}


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: str) -> dict[str, Any]:
    """处置凭证详情：含两套计数口径复算与办结依据。"""
    ticket = review.find_ticket(ticket_id)
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"处置凭证 {ticket_id} 不存在")
    return {
        "ok": True,
        "message": f"处置凭证 {ticket.get('凭证编号')}",
        "ticket": ticket,
        "recompute": review.ticket_recompute(ticket),
        "basis": ticket.get("办结依据"),
    }


@router.post("/tickets/{ticket_id}/settle")
def settle(ticket_id: str, payload: SettlePayload) -> dict[str, Any]:
    """办结凭证：按锁定证据认定异常或关闭告警，可同时启动偏移治理。"""
    return review.settle_ticket(
        ticket_id,
        decision=payload.decision,
        governor=payload.governor,
        remark=payload.remark,
        operator=payload.operator,
    )


@router.get("/records/{identifier}")
def disposition_record(identifier: str) -> dict[str, Any]:
    """异常处置记录查询：返回所用样本计数、计划比例与阈值。"""
    return review.review_audit(identifier)


@router.get("/debug/snapshot")
def debug_snapshot() -> dict[str, Any]:
    """内部排障：一次看清复核台账四张辅助表。"""
    return {
        name: json.loads(json.dumps(store.rows(table), ensure_ascii=False, default=str))
        for name, table in (
            ("files", review.FILES),
            ("evidence", review.EVIDENCE),
            ("signed", review.SIGNED),
            ("tickets", review.TICKETS),
            ("errors", review.ERRORS),
        )
    }
