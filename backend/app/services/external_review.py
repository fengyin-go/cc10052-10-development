"""外部复核凭证领域服务：核对文件的不可变快照、外部更正登记、凭证领取与办结。

数据链路位置：样本治理层的对外证据层。

- 样本量核对按核对版次冻结一次下载文件（计划比例、两组计数、样本总量、阈值），
  文件生成后不再读取实验设计的实时计划比例，之后的计划调整改写不了文件。
- 外部复核回传的更正明细必须关联文件里的实验编码与核对版次；组别缺失或分子大于
  样本总量时登记错误并保留原始行。
- 每条有效更正证据单独留档一份原签收，领取处置凭证时锁定证据，办结时写清采用
  哪套计数口径、是否仍超阈值、是否启动偏移治理。
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from app.store import store

FILES = "aux_review_files"
EVIDENCE = "aux_review_evidence"
SIGNED = "aux_review_evidence_signed"
TICKETS = "aux_review_tickets"
ERRORS = "aux_review_errors"

DEFAULT_THRESHOLD = 0.05


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(r.get("id", 0)) for r in rows), default=0) + 1


def _payload(ok: bool, message: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {"ok": ok, "message": message}
    body.update(extra)
    return body


def _ratio(arm: int, ctrl: int) -> float:
    total = arm + ctrl
    return round(arm / total, 4) if total else 0.0


# ---------------------------------------------------------------------------
# 文件快照
# ---------------------------------------------------------------------------

def find_file(file_id: str) -> dict[str, Any] | None:
    return next(
        (r for r in store.rows(FILES) if str(r.get("文件编号")) == str(file_id)),
        None,
    )


def file_for_task(entry_id: int, revision: str | None = None) -> dict[str, Any] | None:
    for row in store.rows(FILES):
        if int(row.get("核对记录ID", -1)) != int(entry_id):
            continue
        if revision is None or row.get("核对版次") == revision:
            return row
    return None


def file_for_anomaly(entry: dict[str, Any]) -> dict[str, Any] | None:
    code = str(entry.get("异常编号", ""))
    file_id = str(entry.get("核对文件", "") or "")
    for row in store.rows(FILES):
        if file_id and row.get("文件编号") == file_id:
            return row
        if code and row.get("异常编号") == code:
            return row
    # 异常还没挂文件时按核对版次绑定：只能匹配同实验、同版次的冻结文件，
    # 不能把同一实验另一轮的告警错连到这张凭证上。
    revision = str(entry.get("核对版次", "") or "")
    exp = str(entry.get("关联实验编码", "") or entry.get("指标编码", "") or "")
    if revision:
        for row in store.rows(FILES):
            if (
                str(row.get("核对版次")) == revision
                and str(row.get("实验编码")) == exp
                and str(row.get("异常编号")) == code
            ):
                return row
    return None


def _snapshot_csv(file_row: dict[str, Any]) -> str:
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "文件编号", "实验编码", "核对版次", "计划比例", "计划实验组人数",
        "实验组计数", "对照组计数", "样本总量", "实际比例", "偏移量",
        "阈值", "是否偏移超标", "生成时间", "subset_demo", "subset_alternate",
    ])
    writer.writerow([
        _csv_safe(file_row.get("文件编号")),
        _csv_safe(file_row.get("实验编码")),
        _csv_safe(file_row.get("核对版次")),
        _csv_safe(file_row.get("计划比例")),
        _csv_safe(file_row.get("计划实验组人数")),
        _csv_safe(file_row.get("实验组计数")),
        _csv_safe(file_row.get("对照组计数")),
        _csv_safe(file_row.get("样本总量")),
        _csv_safe(file_row.get("实际比例")),
        _csv_safe(file_row.get("偏移量")),
        _csv_safe(file_row.get("阈值")),
        _csv_safe("是" if file_row.get("偏移超标") else "否"),
        _csv_safe(file_row.get("生成时间")),
        _csv_safe(file_row.get("subset_demo")),
        _csv_safe(file_row.get("subset_alternate")),
    ])
    return output.getvalue()


def _csv_safe(value: Any) -> Any:
    text = "" if value is None else str(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _round_tasks(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """同一实验下可选的核对轮次，默认至少包含当前记录自身。"""
    exp = entry.get("实验编码")
    rows = [
        r for r in store.rows("sample_ratio")
        if r.get("实验编码") == exp
    ]
    if not any(int(r.get("id", -1)) == int(entry.get("id", -1)) for r in rows):
        rows.append(entry)
    return rows


def _resolve_round(entry: dict[str, Any], revision: str) -> dict[str, Any] | None:
    """按实验编码 + 核对版次定位要冻结的那一轮核对数据。"""
    task = next(
        (
            r for r in _round_tasks(entry)
            if str(r.get("核对版次", "R1")) == str(revision)
        ),
        None,
    )
    return task


def _file_or_content(file_row: dict[str, Any]) -> str:
    """下载内容按快照字段渲染；种子/历史文件缺正文时就地补齐一次，此后同样冻结。"""
    content = file_row.get("文件内容")
    if not content:
        content = _snapshot_csv(file_row)
        file_row["文件内容"] = content
    return str(content)


def download_rounds(entry: dict[str, Any]) -> list[dict[str, Any]]:
    rounds = []
    for task in sorted(_round_tasks(entry), key=lambda r: str(r.get("核对版次", ""))):
        revision = str(task.get("核对版次", "R1"))
        file_row = file_for_task(int(task.get("id", 0)))
        evidence_rows = [
            r for r in store.rows(EVIDENCE) if r.get("文件编号") == (file_row or {}).get("文件编号")
        ]
        usable = bool(file_row) and any(
            r.get("有效") and not r.get("领取凭证") for r in evidence_rows
        )
        rounds.append({
            "核对记录ID": int(task.get("id", 0)),
            "核对批次": task.get("核对批次"),
            "实验编码": task.get("实验编码"),
            "核对版次": revision,
            "计划比例": task.get("计划比例"),
            "实验组计数": task.get("实验组计数"),
            "对照组计数": task.get("对照组计数"),
            "阈值": task.get("阈值", DEFAULT_THRESHOLD),
            "偏移量": task.get("偏移量"),
            "状态": task.get("status"),
            "已下载": file_row is not None,
            "文件编号": (file_row or {}).get("文件编号"),
            "可用于后续处置": usable,
            "说明": "已登记有效复核证据，可由异常处置领取" if usable else (
                "已下载，待外部复核登记证据" if file_row else "尚未下载"
            ),
        })
    return rounds


def task_detail(entry: dict[str, Any]) -> dict[str, Any]:
    rounds = download_rounds(entry)
    files = [
        r for r in store.rows(FILES)
        if r.get("实验编码") == entry.get("实验编码")
    ]
    return _payload(
        True,
        f"核对记录 {entry.get('核对批次')} 共 {len(rounds)} 轮",
        entry=entry,
        rounds=rounds,
        files=files,
    )


def create_download(
    entry: dict[str, Any],
    *,
    revision: str,
    experiment_count: int | None = None,
    control_count: int | None = None,
    planned_ratio: float | None = None,
    threshold: float | None = None,
) -> dict[str, Any]:
    """按核对版次冻结下载文件；同一版次重复下载返回原文件，不改写。"""
    task = _resolve_round(entry, revision)
    if task is None:
        return _payload(
            False,
            f"核对记录 {entry.get('核对批次')} 下没有版次 {revision}",
        )
    existing = file_for_task(int(task.get("id", 0)), revision)
    if existing is not None:
        # 历史/种子文件若还没落正文，按当时的快照字段补一次，此后同样不再改动。
        _file_or_content(existing)
        return _payload(
            True,
            f"版本 {revision} 已下载，文件内容保持首次生成时不变",
            file=existing,
            immutable=True,
        )

    try:
        planned = float(planned_ratio if planned_ratio is not None else task.get("计划比例", 0.5))
    except (TypeError, ValueError):
        return _payload(False, "计划比例不是合法数值")
    if not 0 < planned < 1:
        return _payload(False, "计划比例必须大于 0 且小于 1")
    # 服务端按原核对版次核验计划比例：已登记的轮次值就是该版次的权威口径。
    registered_planned = task.get("计划比例")
    if registered_planned not in (None, ""):
        try:
            if round(float(registered_planned), 6) != round(planned, 6):
                return _payload(
                    False,
                    f"计划比例与 {revision} 原核对版次不一致：登记为 {registered_planned}，请求为 {planned}",
                )
        except (TypeError, ValueError):
            pass

    try:
        arm = int(experiment_count if experiment_count is not None else task.get("实验组计数", 0))
        ctrl = int(control_count if control_count is not None else task.get("对照组计数", 0))
    except (TypeError, ValueError):
        return _payload(False, "两组计数必须是整数")
    if arm <= 0 or ctrl <= 0:
        return _payload(False, "实验组与对照组计数都必须大于 0")
    # 两组计数同样按原核对版次核验，避免下载时用当前分布覆盖历史快照。
    for field, provided in (("实验组计数", arm), ("对照组计数", ctrl)):
        registered = task.get(field)
        if registered not in (None, ""):
            try:
                if int(registered) != provided:
                    return _payload(
                        False,
                        f"{field} 与 {revision} 原核对版次不一致：登记为 {registered}，请求为 {provided}",
                    )
            except (TypeError, ValueError):
                pass

    try:
        limit = float(threshold if threshold is not None else task.get("阈值", DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        return _payload(False, "阈值不是合法数值")
    if not 0 < limit < 1:
        return _payload(False, "阈值必须大于 0 且小于 1")

    planned_arm = round(planned * (arm + ctrl), 2)
    actual = _ratio(arm, ctrl)
    offset = round(abs(actual - planned), 4)
    file_id = f"SR-{task.get('实验编码')}-{revision}"
    anomaly_code = str(task.get("异常编号") or f"AN-SR-{task.get('id')}")

    file_row: dict[str, Any] = {
        "id": _next_id(store.rows(FILES)),
        "文件编号": file_id,
        "核对记录ID": int(task.get("id", 0)),
        "核对批次": task.get("核对批次"),
        "实验编码": task.get("实验编码"),
        "核对版次": revision,
        "计划比例": str(planned),
        "计划实验组人数": str(planned_arm),
        "实验组计数": str(arm),
        "对照组计数": str(ctrl),
        "样本总量": str(arm + ctrl),
        "实际比例": str(actual),
        "偏移量": str(offset),
        "阈值": str(limit),
        "偏移超标": offset > limit,
        "生成时间": _now(),
        "异常编号": anomaly_code,
        "指标编码": task.get("指标编码") or task.get("实验编码"),
        "subset_demo": str(round(planned * 10, 4)),
        "subset_alternate": str(round(planned * 20, 4)),
        "status": "已下载",
        "pending": True,
        "abnormal": offset > limit,
    }
    file_row["文件内容"] = _snapshot_csv(file_row)
    store.rows(FILES).append(file_row)

    task["下载文件"] = file_id
    task["下载时间"] = file_row["生成时间"]

    # 同一实验下如已有被标记告警的异常事件，挂上文件编号供异常页打开凭证。
    anomaly = _attach_anomaly(anomaly_code, file_row)
    if anomaly is not None:
        anomaly["核对文件"] = file_id
    return _payload(
        True,
        f"已按核对版次 {revision} 生成下载文件 {file_id}（偏移 {offset}）",
        file=file_row,
    )


def _attach_anomaly(anomaly_code: str, file_row: dict[str, Any]) -> dict[str, Any] | None:
    rows = store.rows("anomaly_metric")
    target = next((r for r in rows if r.get("异常编号") == anomaly_code), None)
    if target is not None:
        return target
    exp = file_row.get("实验编码")
    target = next(
        (
            r for r in rows
            if r.get("关联实验编码") == exp and r.get("status") == "待确认"
        ),
        None,
    )
    if target is None and file_row.get("偏移超标"):
        target = {
            "id": _next_id(rows),
            "异常编号": anomaly_code,
            "指标编码": exp,
            "基线区间": f"计划比例 {file_row.get('计划比例')}",
            "观测值": f"实验组 {file_row.get('实验组计数')} / 对照组 {file_row.get('对照组计数')}",
            "异常评分": "0.8",
            "status": "待确认",
            "pending": True,
            "abnormal": False,
            "关联实验编码": exp,
            "核对版次": file_row.get("核对版次"),
        }
        rows.append(target)
    return target


# ---------------------------------------------------------------------------
# 外部更正登记
# ---------------------------------------------------------------------------

def _record_error(
    file_row: dict[str, Any],
    *,
    revision: str,
    reviewer: str,
    errors: list[str],
    raw_row: dict[str, Any],
) -> dict[str, Any]:
    rows = store.rows(ERRORS)
    row = {
        "id": _next_id(rows),
        "错误编号": f"ER-ERR-{len(rows) + 1}",
        "文件编号": file_row.get("文件编号"),
        "实验编码": file_row.get("实验编码"),
        "核对版次": file_row.get("核对版次"),
        "提交版次": revision,
        "复核人": reviewer,
        "错误记录": "；".join(errors),
        "原始行": json.dumps(raw_row, ensure_ascii=False, default=str),
        "status": "登记错误",
        "记录时间": _now(),
    }
    rows.append(row)
    return row


def _recompute(arm: int, ctrl: int, planned: float, limit: float) -> dict[str, Any]:
    actual = _ratio(arm, ctrl)
    offset = round(abs(actual - planned), 4)
    return {
        "实验组计数": arm,
        "对照组计数": ctrl,
        "样本总量": arm + ctrl,
        "实际比例": str(actual),
        "偏移量": str(offset),
        "阈值": str(limit),
        "超阈值": offset > limit,
    }


def submit_evidence(
    file_row: dict[str, Any],
    *,
    revision: str,
    experiment_code: str,
    reviewer: str,
    group_counts: dict[str, Any] | None,
    submitted_numerator: int | None = None,
    submitted_denominator: int | None = None,
    adjustment: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    raw_row = {
        "file_id": file_row.get("文件编号"),
        "实验编码": experiment_code,
        "核对版次": revision,
        "复核人": reviewer,
        "group_counts": group_counts or {},
        "submitted_numerator": submitted_numerator,
        "submitted_denominator": submitted_denominator,
        "adjustment": adjustment or {},
        "note": note,
    }
    errors: list[str] = []
    if experiment_code != file_row.get("实验编码"):
        errors.append(
            f"实验编码不一致：文件为 {file_row.get('实验编码')}，提交为 {experiment_code}"
        )
    if revision != file_row.get("核对版次"):
        errors.append(
            f"核对版次不一致：文件为 {file_row.get('核对版次')}，提交为 {revision}"
        )
    groups = group_counts or {}
    if not groups:
        errors.append("缺少外部复核的组别计数")
    else:
        missing = [name for name in ("实验组", "对照组") if groups.get(name) is None]
        if missing:
            errors.append(f"缺少外部复核的组别：{'、'.join(missing)}")
    total = int(file_row.get("样本总量", 0) or 0)
    if submitted_numerator is not None:
        try:
            numerator = int(submitted_numerator)
        except (TypeError, ValueError):
            numerator = -1
        denominator = total
        if submitted_denominator is not None:
            try:
                denominator = int(submitted_denominator)
            except (TypeError, ValueError):
                errors.append("提交的分母必须是整数")
                denominator = total
        if numerator <= 0:
            errors.append("提交的分子必须大于 0")
        if numerator > denominator:
            errors.append(f"提交的分子 {numerator} 大于样本总量 {denominator}")

    arm: int | None = None
    ctrl: int | None = None
    if not errors:
        try:
            arm = int(groups.get("实验组"))
            ctrl = int(groups.get("对照组"))
        except (TypeError, ValueError):
            errors.append("两组计数必须是整数")
        else:
            if arm < 0 or ctrl < 0:
                errors.append("两组计数不能为负")
            if arm + ctrl != total:
                errors.append(f"两组计数之和 {arm + ctrl} 与文件样本总量 {total} 不一致")

    moved = 0
    target_arm = ""
    if submission_adjustment := adjustment or {}:
        target_arm = str(submission_adjustment.get("转入组别", ""))
        try:
            moved = int(submission_adjustment.get("人数", 0))
        except (TypeError, ValueError):
            errors.append("更正调整人数必须是整数")
            moved = 0
        if moved < 0:
            errors.append("更正调整人数不能为负")
        if moved and target_arm not in ("实验组", "对照组"):
            errors.append("更正必须指定转入的组别")

    if errors or arm is None or ctrl is None:
        error_row = _record_error(
            file_row,
            revision=revision,
            reviewer=reviewer,
            errors=errors,
            raw_row=raw_row,
        )
        return _payload(
            False,
            "外部复核登记失败：" + "；".join(errors),
            errors=errors,
            raw_row=raw_row,
            error_record=error_row,
        )

    planned = float(file_row.get("计划比例", "0.5"))
    limit = float(file_row.get("阈值", DEFAULT_THRESHOLD))
    recompute = _recompute(arm, ctrl, planned, limit)
    changed = arm != int(file_row.get("实验组计数", arm)) or ctrl != int(
        file_row.get("对照组计数", ctrl)
    )
    evidence_rows = store.rows(EVIDENCE)
    same_file = [
        r for r in evidence_rows if r.get("文件编号") == file_row.get("文件编号")
    ]
    evidence_id = f"ER-{file_row.get('文件编号')}-{len(same_file) + 1}"
    evidence: dict[str, Any] = {
        "id": _next_id(evidence_rows),
        "证据编号": evidence_id,
        "文件编号": file_row.get("文件编号"),
        "实验编码": file_row.get("实验编码"),
        "核对版次": file_row.get("核对版次"),
        "复核人": reviewer,
        "实验组计数": str(arm),
        "对照组计数": str(ctrl),
        "样本总量": str(arm + ctrl),
        "原实验组计数": str(file_row.get("实验组计数")),
        "原对照组计数": str(file_row.get("对照组计数")),
        "更正调整人数": str(moved),
        "转入组别": target_arm,
        "修订分子": submitted_numerator,
        "修订分母": submitted_denominator,
        "说明": note,
        "计划比例": str(planned),
        "阈值": str(limit),
        "复算": recompute,
        "有效": True,
        "status": "已签收",
        "pending": False,
        "abnormal": recompute["超阈值"],
        "原始行": json.dumps(raw_row, ensure_ascii=False, default=str),
        "登记时间": _now(),
        "领取凭证": "",
        "可领取": True,
        "采用计数口径": "修订计数" if changed else "下载时计数",
    }
    evidence_rows.append(evidence)

    signed_rows = store.rows(SIGNED)
    signed = dict(evidence)
    signed["id"] = _next_id(signed_rows)
    signed["原签收"] = True
    signed["签收时间"] = evidence["登记时间"]
    signed["签收留档编号"] = f"SIGN-{evidence_id}"
    signed_rows.append(signed)

    anomaly = _attach_anomaly(str(file_row.get("异常编号")), file_row)
    if anomaly is not None:
        anomaly["复核证据"] = evidence_id
        anomaly["证据状态"] = "已签收"
        anomaly["修订实验组计数"] = str(arm)
        anomaly["修订对照组计数"] = str(ctrl)
        anomaly["修订偏移量"] = recompute["偏移量"]
        anomaly["pending"] = True
    return _payload(
        True,
        f"复核证据 {evidence_id} 已登记并独立留档原签收（修订偏移 {recompute['偏移量']}）",
        evidence=evidence,
        recompute=recompute,
    )


def signed_copy(evidence_id: str) -> dict[str, Any]:
    row = next(
        (r for r in store.rows(SIGNED) if r.get("证据编号") == evidence_id),
        None,
    )
    if row is None:
        return _payload(False, "没有独立留档的原签收")
    return _payload(True, "原签收留档已读取", signed=row)


# ---------------------------------------------------------------------------
# 凭证领取与办结
# ---------------------------------------------------------------------------

def find_evidence(identifier: str) -> dict[str, Any] | None:
    return next(
        (r for r in store.rows(EVIDENCE) if str(r.get("证据编号")) == str(identifier)),
        None,
    )


def find_ticket(identifier: str) -> dict[str, Any] | None:
    for row in store.rows(TICKETS):
        if str(row.get("凭证编号")) == str(identifier):
            return row
    if str(identifier).isdigit():
        return store.find(TICKETS, int(str(identifier)))
    return None


def _ticket_counts(ticket: dict[str, Any], key: str) -> tuple[int, int]:
    return int(ticket.get(f"{key}实验组计数", 0)), int(ticket.get(f"{key}对照组计数", 0))


def ticket_recompute(ticket: dict[str, Any]) -> dict[str, Any]:
    planned = float(ticket.get("计划比例", "0.5"))
    limit = float(ticket.get("阈值", DEFAULT_THRESHOLD))
    downloaded = _ticket_counts(ticket, "下载")
    revised = _ticket_counts(ticket, "修订")
    downloaded_actual = _ratio(*downloaded)
    revised_actual = _ratio(*revised)
    downloaded_offset = round(abs(downloaded_actual - planned), 4)
    revised_offset = round(abs(revised_actual - planned), 4)
    return {
        "计划比例": str(planned),
        "下载实验组计数": downloaded[0],
        "下载对照组计数": downloaded[1],
        "下载实际比例": str(downloaded_actual),
        "下载偏移量": str(downloaded_offset),
        "下载超阈值": downloaded_offset > limit,
        "修订实验组计数": revised[0],
        "修订对照组计数": revised[1],
        "修订实际比例": str(revised_actual),
        "修订偏移量": str(revised_offset),
        "修订超阈值": revised_offset > limit,
        "阈值": str(limit),
    }


def claim_ticket(
    evidence_id: str,
    *,
    anomaly_entry_id: int | None = None,
    operator: str = "",
) -> dict[str, Any]:
    evidence = find_evidence(evidence_id)
    if evidence is None:
        return _payload(False, f"复核证据 {evidence_id} 不存在")
    if not evidence.get("有效"):
        return _payload(False, f"复核证据 {evidence_id} 不是有效证据，不能领取")
    if evidence.get("领取凭证"):
        return _payload(
            False,
            f"复核证据 {evidence_id} 已被凭证 {evidence.get('领取凭证')} 领取，不能重复领取",
        )
    file_row = find_file(str(evidence.get("文件编号")))
    if file_row is None:
        return _payload(False, "复核文件缺失，无法领取")
    if anomaly_entry_id is not None:
        anomaly = store.find("anomaly_metric", anomaly_entry_id)
        if anomaly is None:
            return _payload(False, f"异常事件 {anomaly_entry_id} 不存在")
        if str(anomaly.get("异常编号")) != str(file_row.get("异常编号")):
            return _payload(
                False,
                f"异常 {anomaly.get('异常编号')} 与复核文件 {file_row.get('文件编号')} "
                f"（异常 {file_row.get('异常编号')}）不匹配，不能领取",
            )

    ticket_id = f"RV-SR-{_next_id(store.rows(TICKETS))}"
    conflict = evidence.get("采用计数口径") == "修订计数"
    ticket: dict[str, Any] = {
        "id": _next_id(store.rows(TICKETS)),
        "凭证编号": ticket_id,
        "证据编号": evidence.get("证据编号"),
        "文件编号": file_row.get("文件编号"),
        "核对批次": file_row.get("核对批次"),
        "核对记录ID": file_row.get("核对记录ID"),
        "实验编码": file_row.get("实验编码"),
        "核对版次": file_row.get("核对版次"),
        "异常编号": file_row.get("异常编号"),
        "指标编码": file_row.get("指标编码"),
        "关联实验编码": file_row.get("实验编码"),
        "计划比例": file_row.get("计划比例"),
        "阈值": file_row.get("阈值", DEFAULT_THRESHOLD),
        "下载实验组计数": file_row.get("实验组计数"),
        "下载对照组计数": file_row.get("对照组计数"),
        "修订实验组计数": evidence.get("实验组计数"),
        "修订对照组计数": evidence.get("对照组计数"),
        "更正调整人数": evidence.get("更正调整人数", "0"),
        "证据状态": evidence.get("status"),
        "有效证据": True,
        "计数口径冲突": (
            f"下载时计数 {file_row.get('实验组计数')}/{file_row.get('对照组计数')} "
            f"与修订计数 {evidence.get('实验组计数')}/{evidence.get('对照组计数')} 均有效"
        ) if conflict else "",
        "采信证据": evidence.get("证据编号"),
        "采用计数口径": evidence.get("采用计数口径"),
        "status": "待处置",
        "pending": True,
        "abnormal": False,
        "领取人": operator,
        "领取时间": _now(),
    }
    store.rows(TICKETS).append(ticket)

    evidence["领取凭证"] = ticket_id
    evidence["可领取"] = False

    anomaly = _attach_anomaly(str(file_row.get("异常编号")), file_row)
    if anomaly is None and anomaly_entry_id is not None:
        anomaly = store.find("anomaly_metric", anomaly_entry_id)
    if anomaly is not None:
        anomaly["凭证编号"] = ticket_id
        anomaly["复核证据"] = evidence.get("证据编号")
        anomaly["核对文件"] = file_row.get("文件编号")
        anomaly["核对版次"] = file_row.get("核对版次")
        anomaly["关联实验编码"] = file_row.get("实验编码")
        anomaly["evidence_status"] = "已领取"
        if anomaly.get("status") in ("待确认", "待处置"):
            anomaly["status"] = "待处置"
            anomaly["pending"] = True
    return _payload(
        True,
        f"已领取处置凭证 {ticket_id}，锁定有效证据 {evidence.get('证据编号')}",
        ticket=ticket,
        recompute=ticket_recompute(ticket),
        conflict=conflict,
    )


def settle_ticket(
    identifier: str,
    *,
    decision: str,
    governor: bool = False,
    remark: str = "",
    operator: str = "",
) -> dict[str, Any]:
    ticket = find_ticket(identifier)
    if ticket is None:
        return _payload(False, "处置凭证不存在")
    if ticket.get("status") == "已办结":
        return _payload(False, f"凭证 {ticket.get('凭证编号')} 已办结，不能重复办结")
    if decision not in ("confirm", "close"):
        return _payload(False, "办结结论必须是 confirm（确认异常）或 close（关闭告警）")

    evidence = find_evidence(str(ticket.get("证据编号")))
    file_row = find_file(str(ticket.get("文件编号")))
    if evidence is None or not evidence.get("有效"):
        return _payload(False, "凭证没有关联有效复核证据，禁止确认或关闭告警")
    if file_row is None:
        return _payload(False, "凭证没有关联核对文件，禁止确认或关闭告警")

    recompute = ticket_recompute(ticket)
    revised_offset = float(recompute["修订偏移量"])
    downloaded_offset = float(recompute["下载偏移量"])
    plan_limit = float(recompute["阈值"])
    adopted = "修订计数" if evidence.get("采用计数口径") == "修订计数" else "下载时计数"

    if decision == "close" and revised_offset > plan_limit:
        return _payload(
            False,
            f"修订计数偏移 {revised_offset} 仍超阈值 {plan_limit}，不能关闭告警",
            recompute=recompute,
        )

    now = _now()
    if revised_offset > plan_limit:
        anomaly_status = "已确认"
        outcome = (
            f"确认异常：采用{adopted}（证据 {evidence.get('证据编号')}）"
            f"修订偏移 {revised_offset} 超阈值 {plan_limit}"
        )
    else:
        anomaly_status = "误报"
        outcome = (
            f"关闭告警：采用{adopted}（证据 {evidence.get('证据编号')}）"
            f"修订偏移 {revised_offset} 在阈值 {plan_limit} 内"
        )
    start_governance = bool(governor) and revised_offset > plan_limit
    basis = {
        "采用证据": evidence.get("证据编号"),
        "证据有效": True,
        "采用计数口径": adopted,
        "计划比例": recompute["计划比例"],
        "下载实验组计数": recompute["下载实验组计数"],
        "下载对照组计数": recompute["下载对照组计数"],
        "修订实验组计数": recompute["修订实验组计数"],
        "修订对照组计数": recompute["修订对照组计数"],
        "下载时偏移": downloaded_offset,
        "修订后偏移": revised_offset,
        "阈值": plan_limit,
        "是否仍超阈值": revised_offset > plan_limit,
        "是否启动偏移治理": start_governance,
        "异常状态": anomaly_status,
        "办结结论": outcome,
        "办结人": operator,
        "办结时间": now,
    }
    ticket.update({
        "status": "已办结",
        "pending": False,
        "abnormal": revised_offset > plan_limit,
        "办结结论": outcome,
        "实际采用计数口径": adopted,
        "实际采用证据": evidence.get("证据编号"),
        "办结依据": basis,
        "办结时间": now,
        "备注": remark,
    })
    evidence["status"] = "已办结"

    anomaly_rows = store.rows("anomaly_metric")
    target = next(
        (r for r in anomaly_rows if r.get("异常编号") == ticket.get("异常编号")),
        None,
    )
    if target is not None:
        target["status"] = anomaly_status
        target["pending"] = False
        target["异常处置"] = ticket.get("凭证编号")
        target["处置依据"] = basis
        target["处置采用的计数口径"] = adopted
        target["启动偏移治理"] = start_governance
        target["处置计划比例"] = recompute["计划比例"]
        target["处置阈值"] = str(plan_limit)
        target["处置时实验组计数"] = recompute["修订实验组计数"]
        target["处置时对照组计数"] = recompute["修订对照组计数"]

    if start_governance:
        _open_governance(ticket, recompute, now)

    # 核对批次随处置办结；仍超阈值并启动治理时保留待办标记，便于后续治理追踪。
    task = next(
        (r for r in store.rows("sample_ratio") if r.get("核对批次") == ticket.get("核对批次")),
        None,
    )
    if task is not None:
        task["status"] = "已办结"
        task["pending"] = start_governance
        task["办结凭证"] = ticket.get("凭证编号")
        task["办结依据"] = basis
    return _payload(
        True,
        f"凭证 {ticket.get('凭证编号')} 已办结：{outcome}",
        ticket=ticket,
        anomaly=target,
        recompute=recompute,
        basis=basis,
    )


def _open_governance(ticket: dict[str, Any], recompute: dict[str, Any], now: str) -> None:
    code = f"AN-SR-G-{ticket.get('文件编号')}"
    rows = store.rows("anomaly_metric")
    if any(r.get("异常编号") == code for r in rows):
        return
    rows.append({
        "id": _next_id(rows),
        "异常编号": code,
        "指标编码": ticket.get("实验编码"),
        "基线区间": f"计划比例 {recompute['计划比例']}",
        "观测值": f"修订偏移 {recompute['修订偏移量']}",
        "异常评分": "0.7",
        "status": "待确认",
        "pending": True,
        "abnormal": False,
        "关联实验编码": ticket.get("关联实验编码"),
        "核对版次": ticket.get("核对版次"),
        "来源凭证": ticket.get("凭证编号"),
        "治理来源": "复核后仍超阈值",
    })
    task = next(
        (r for r in store.rows("sample_ratio") if r.get("核对批次") == ticket.get("核对批次")),
        None,
    )
    if task is not None:
        task["偏移治理"] = code


def anomaly_material(entry: dict[str, Any]) -> dict[str, Any]:
    """异常确认/关闭前必须消费核对文件对应的有效复核证据。"""
    file_row = file_for_anomaly(entry)
    if file_row is None:
        return _payload(False, f"异常 {entry.get('异常编号')} 没有对应的核对文件")
    tickets = [
        r for r in store.rows(TICKETS)
        if r.get("文件编号") == file_row.get("文件编号")
    ]
    evidence = [
        r for r in store.rows(EVIDENCE)
        if r.get("文件编号") == file_row.get("文件编号") and r.get("有效")
    ]
    return _payload(
        True,
        "核对文件与复核证据已就绪",
        file=file_row,
        tickets=tickets,
        evidence=evidence,
    )


def anomaly_detail(entry: dict[str, Any]) -> dict[str, Any]:
    material = anomaly_material(entry)
    if not material.get("ok"):
        return {**material, "entry": entry, "file": None, "tickets": [], "evidence": []}
    active = [
        ticket for ticket in material["tickets"]
        if ticket.get("status") == "待处置"
    ]
    return {
        **material,
        "entry": entry,
        "active_ticket": active[-1] if active else None,
    }


def review_audit(identifier: str) -> dict[str, Any]:
    """异常处置记录查询：按凭证编号或异常编号返回所用样本与阈值。"""
    ticket = find_ticket(identifier)
    if ticket is None:
        rows = store.rows("anomaly_metric")
        target = next(
            (r for r in rows if r.get("异常编号") == str(identifier)),
            None,
        )
        if target is not None and target.get("异常处置"):
            ticket = find_ticket(str(target.get("异常处置")))
    if ticket is None:
        return _payload(False, "没有找到对应的处置记录")
    return _payload(
        True,
        f"处置凭证 {ticket.get('凭证编号')} 办结记录",
        ticket=ticket,
        recompute=ticket_recompute(ticket),
        basis=ticket.get("办结依据"),
    )
