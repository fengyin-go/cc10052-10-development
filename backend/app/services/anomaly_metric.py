"""指标异常检测领域服务：管理指标值异常波动的基线对比、异常评分与误报标记。

数据链路位置：质量检测层，消费指标计算结果与时效监控告警，检测指标异常波动，
把确认异常推送给结论评审队列进入人工处置。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class AnomalyMetricService(BaseModuleService):
    MODULE = "anomaly_metric"
    ENTITY = "异常事件"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["异常编号", "指标编码", "基线区间", "观测值", "异常评分"]
    REQUIRED_FIELDS = ["异常编号", "指标编码", "基线区间"]
    STATUS_ORDER = ["待确认", "已确认", "误报"]
    ACTION_RULES = {"确认异常": "已确认", "标记误报": "误报"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：异常检测与人工处置 ----

    def run_action(self, entry_id: int, action: str, remark: str = "") -> tuple[dict[str, Any] | None, str]:
        """动作入口统一走领域逻辑：有待处置凭证时经凭证办结，否则校验复核证据。"""
        if action in ("确认异常", "标记误报"):
            from app.services import external_review

            entry = store.find(self.MODULE, entry_id)
            if entry is None:
                return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
            pending = self._pending_ticket(entry)
            if pending is not None:
                result = external_review.settle_ticket(
                    str(pending.get("凭证编号")),
                    decision="confirm" if action == "确认异常" else "close",
                    remark=remark,
                )
                return (entry if result.get("ok") else None), str(result.get("message", ""))
            if action == "确认异常":
                result = self.confirm_anomaly(entry_id)
            else:
                result = self.mark_false_positive(entry_id, remark or "人工标记误报")
            return (entry if result.get("ok") else None), str(result.get("message", ""))
        return super().run_action(entry_id, action, remark)

    def _pending_ticket(self, entry: dict[str, Any]) -> dict[str, Any] | None:
        from app.services import external_review

        code = entry.get("凭证编号")
        if not code:
            return None
        ticket = external_review.find_ticket(str(code))
        if ticket is not None and ticket.get("status") == "待处置":
            return ticket
        return None

    def confirm_anomaly(self, entry_id: int) -> dict[str, Any]:
        """确认异常并把处置单推送到结论评审队列。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") not in ("待确认", "待处置"):
            return {"ok": False, "message": "仅待确认异常可确认"}
        gate = self._require_review_evidence(entry, closing=False)
        if gate is not None:
            return gate
        entry["status"] = "已确认"
        entry["pending"] = False
        entry["确认时间"] = datetime.now(timezone.utc).isoformat()
        review_rows = store.rows("conclusion_review")
        review_rows.append({
            "id": max((int(r.get("id", 0)) for r in review_rows), default=0) + 1,
            "记录编号": f"RV-AN-{entry_id}",
            "实验编码": str(entry.get("关联实验编码") or entry.get("指标编码", "")).replace("M-", "EXP_-"),
            "评审结论": f"指标异常 {entry.get('异常编号', '')} 待评审处置",
            "评审角色": "数据质量",
            "评审时间": datetime.now(timezone.utc).isoformat(),
            "status": "待评审",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"异常 {entry.get('异常编号')} 已确认并进入评审"}

    def mark_false_positive(self, entry_id: int, reason: str) -> dict[str, Any]:
        """标记误报并把原因写回数据时效监控修正告警。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") not in ("待确认", "待处置"):
            return {"ok": False, "message": "仅待确认异常可标记误报"}
        if not reason.strip():
            return {"ok": False, "message": "标记误报必须填写原因"}
        gate = self._require_review_evidence(entry, closing=True)
        if gate is not None:
            return gate
        entry["status"] = "误报"
        entry["pending"] = False
        entry["abnormal"] = True
        entry["误报原因"] = reason
        source = str(entry.get("异常编号", ""))
        if source.startswith("AN-DF-"):
            target_id = source.replace("AN-DF-", "").split("-")[0]
            if target_id.isdigit():
                fresh = store.find("data_freshness", int(target_id))
                if fresh is not None:
                    fresh["status"] = "新鲜"
                    fresh["时效等级"] = "新鲜"
                    fresh["pending"] = False
        return {"ok": True, "message": f"已标记误报（{reason}），来源告警已修正"}

    def _require_review_evidence(self, entry: dict[str, Any], *, closing: bool) -> dict[str, Any] | None:
        """确认或关闭异常前必须消费核对文件对应的有效复核证据。

        仅在告警已关联样本量核对文件时启用；与样本偏移无关的普通指标异常保持原流转。
        """
        from app.services import external_review

        linked = (
            str(entry.get("异常编号", "")).startswith("AN-SR")
            or entry.get("核对文件")
            or entry.get("核对版次")
            or entry.get("关联实验编码")
        )
        # 复核后仍超阈值时登记的偏移治理任务不是一组新的下载告警，
        # 它承接的是同一份已办结凭证，按普通异常流转即可。
        if entry.get("治理来源") or "AN-SR-G" in str(entry.get("异常编号", "")):
            linked = False
        if not linked:
            return None
        material = external_review.anomaly_material(entry)
        if not material.get("ok"):
            return {
                "ok": False,
                "message": (
                    f"异常 {entry.get('异常编号')} 必须先关联样本量核对文件，"
                    "并在异常处置页领取有效复核凭证后才能办理"
                ),
            }
        tickets = [
            ticket for ticket in material.get("tickets", [])
            if ticket.get("status") == "待处置"
        ]
        evidence = [
            item for item in material.get("evidence", [])
            if item.get("有效")
        ]
        if not tickets or not evidence:
            return {
                "ok": False,
                "message": (
                    f"异常 {entry.get('异常编号')} 缺少有效复核证据，"
                    "请先在样本量核对登记外部复核并领取处置凭证"
                ),
            }
        ticket = tickets[-1]
        recomputed = external_review.ticket_recompute(ticket)
        if closing:
            threshold = float(ticket.get("阈值", 0.05))
            # 直接用登记凭证时的口径复算，避免关闭时误采信已过时的下载计数。
            if float(recomputed["修订偏移量"]) > threshold:
                return {
                    "ok": False,
                    "message": (
                        f"凭证 {ticket.get('凭证编号')} 的修订计数偏移 "
                        f"{recomputed['修订偏移量']} 仍超阈值 {threshold}，不能关闭告警"
                    ),
                }
            entry["处置时修订偏移"] = recomputed["修订偏移量"]
        entry["处置凭证"] = ticket.get("凭证编号")
        entry["处置证据"] = ticket.get("证据编号")
        entry["处置采用的计数口径"] = ticket.get("采用计数口径")
        return None

    def detect_deviation(self, metric_code: str, observed: float, baseline: float) -> dict[str, Any]:
        """对比观测值与基线，偏差超阈值时登记新异常事件。"""
        if not metric_code:
            return {"ok": False, "message": "指标编码为空"}
        if baseline == 0:
            deviation = abs(observed)
        else:
            deviation = abs(observed - baseline) / abs(baseline)
        if deviation < 0.2:
            return {"ok": True, "message": f"偏差 {deviation:.2%} 未超阈值", "anomaly": False}
        rows = store.rows(self.MODULE)
        rows.append({
            "id": max((int(r.get("id", 0)) for r in rows), default=0) + 1,
            "异常编号": f"AN-DV-{metric_code}-{datetime.now(timezone.utc).timestamp():.0f}",
            "指标编码": metric_code,
            "基线区间": f"[{baseline}, {baseline}]",
            "观测值": str(observed),
            "异常评分": str(round(min(deviation, 1.0), 2)),
            "status": "待确认",
            "pending": True,
            "abnormal": False,
        })
        return {"ok": True, "message": f"偏差 {deviation:.2%} 超阈值，异常已登记", "anomaly": True}

    def batch_confirm(self) -> dict[str, Any]:
        """批量确认全部高分待确认异常。"""
        pending = [
            r for r in store.rows(self.MODULE)
            if r.get("status") == "待确认" and float(str(r.get("异常评分", "0")) or 0) >= 0.7
        ]
        done = sum(1 for r in pending if self.confirm_anomaly(int(r.get("id", 0))).get("ok"))
        return {"ok": True, "message": f"高分异常批量确认 {done} 条"}

    def consume_from_metric_compute(self, metric_code: str) -> list[float]:
        """读取指标计算层最近作业产出，构造观测值序列。"""
        values = []
        for r in store.rows("metric_compute"):
            if r.get("指标编码") == metric_code and r.get("status") == "已完成":
                value = str(r.get("作业名称", "")).split("-")[-1]
                if value.replace(".", "").isdigit():
                    values.append(float(value))
        return values

    def consume_from_metric_catalog(self, metric_code: str) -> dict[str, Any]:
        """读取指标口径中心的口径版本，锁定异常检测的口径上下文。"""
        metric = next((m for m in store.rows("metric_catalog") if m.get("指标编码") == metric_code), None)
        if metric is None:
            return {"ok": False, "message": "口径不存在"}
        return {"ok": True, "口径版本": metric.get("口径版本", ""), "聚合窗口": metric.get("聚合窗口", "")}

    def export_confirmed(self, metric_code: str) -> list[dict[str, Any]]:
        """导出已确认异常清单，供报告生成器写入风险附注。"""
        return [
            {
                "异常编号": r.get("异常编号", ""),
                "观测值": r.get("观测值", ""),
                "异常评分": r.get("异常评分", ""),
                "确认时间": r.get("确认时间", ""),
            }
            for r in store.rows(self.MODULE)
            if r.get("指标编码") == metric_code and r.get("status") == "已确认"
        ]
