"""样本量核对领域服务：管理实验分组比例的计划与实际核对、偏移告警与阈值判定。

数据链路位置：样本治理层，消费分组分配器的实际分配结果与实验设计的计划比例，
核对偏移量并告警，保证实验分组的统计有效性。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class SampleRatioService(BaseModuleService):
    MODULE = "sample_ratio"
    ENTITY = "核对结果"
    OPERATION_TERM = "校验"
    LIST_FIELDS = ["核对批次", "实验编码", "核对版次", "计划比例", "实际比例", "偏移量"]
    REQUIRED_FIELDS = ["核对批次", "实验编码", "计划比例"]
    STATUS_ORDER = ["待核对", "已核对", "偏移超标", "待处置", "已办结"]
    ACTION_RULES = {"执行核对": "已核对", "告警偏移": "偏移超标"}
    NEGATIVE_ACTIONS = []

    # ---- 业务扩展：比例核对与偏移告警 ----

    def run_action(self, entry_id: int, action: str, remark: str = "") -> tuple[dict[str, Any] | None, str]:
        """动作入口走领域逻辑：核对按登记计数复算，告警登记异常事件。"""
        if action == "执行核对":
            result = self.run_check(entry_id)
        elif action == "告警偏移":
            result = self.alert_offset(entry_id)
        else:
            return super().run_action(entry_id, action, remark)
        entry = store.find(self.MODULE, entry_id)
        return (entry if result.get("ok") else None), str(result.get("message", ""))

    def run_check(self, entry_id: int) -> dict[str, Any]:
        """执行比例核对：重算实际比例并按阈值判定偏移状态。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "待核对":
            return {"ok": False, "message": "仅待核对批次可执行"}
        exp_code = str(entry.get("实验编码", ""))
        # 优先使用核对批次已登记的两组计数（外部复核下载时冻结的口径），
        # 没有登记时才回落到分组分配器的实时数据。
        try:
            arm = int(entry.get("实验组计数", ""))
            ctrl = int(entry.get("对照组计数", ""))
        except (TypeError, ValueError):
            arm, ctrl = -1, -1
        if arm < 0 or ctrl < 0:
            assign_rows = [
                r for r in store.rows("variant_assign")
                if r.get("实验编码") == exp_code and r.get("status") != "已剔除"
            ]
            if not assign_rows:
                entry["status"] = "偏移超标"
                entry["abnormal"] = True
                return {"ok": False, "message": "实验无有效分组，核对失败"}
            arm = sum(1 for r in assign_rows if r.get("分组名称") == "实验组")
            ctrl = sum(1 for r in assign_rows if r.get("分组名称") == "对照组")
            entry["实验组计数"] = str(arm)
            entry["对照组计数"] = str(ctrl)
        if arm + ctrl == 0:
            entry["status"] = "偏移超标"
            entry["abnormal"] = True
            return {"ok": False, "message": "两组计数为空，核对失败"}
        actual = round(arm / (arm + ctrl), 4)
        try:
            planned = float(entry.get("计划比例", "0.5"))
        except ValueError:
            planned = 0.5
        try:
            limit = float(entry.get("阈值", "0.05"))
        except ValueError:
            limit = 0.05
        offset = round(abs(actual - planned), 4)
        entry["实际比例"] = str(actual)
        entry["偏移量"] = str(offset)
        entry["核对时间"] = datetime.now(timezone.utc).isoformat()
        if offset > limit:
            entry["status"] = "偏移超标"
            entry["pending"] = True
            entry["abnormal"] = True
            return {"ok": True, "message": f"偏移 {offset} 超阈值 {limit}，已告警"}
        entry["status"] = "已核对"
        entry["pending"] = False
        return {"ok": True, "message": f"核对通过，偏移 {offset} 在阈值内"}

    def alert_offset(self, entry_id: int) -> dict[str, Any]:
        """把偏移超标批次登记为异常事件，进入质量处置流程。"""
        entry = store.find(self.MODULE, entry_id)
        if entry is None or entry.get("status") != "偏移超标":
            return {"ok": False, "message": "仅偏移超标批次可告警"}
        anomaly_rows = store.rows("anomaly_metric")
        anomaly_code = str(entry.get("异常编号") or f"AN-SR-{entry_id}")
        existing = next(
            (r for r in anomaly_rows if r.get("异常编号") == anomaly_code),
            None,
        )
        if existing is None:
            existing = {
                "id": max((int(r.get("id", 0)) for r in anomaly_rows), default=0) + 1,
                "异常编号": anomaly_code,
                "指标编码": str(entry.get("实验编码", "")),
                "基线区间": str(entry.get("计划比例", "")),
                "观测值": str(entry.get("实际比例", "")),
                "异常评分": "0.7",
                "status": "待确认",
                "pending": True,
                "abnormal": False,
            }
            anomaly_rows.append(existing)
        existing["关联实验编码"] = str(entry.get("实验编码", ""))
        existing["核对版次"] = str(entry.get("核对版次", "R1"))
        existing["核对批次"] = str(entry.get("核对批次", ""))
        entry["异常编号"] = anomaly_code
        entry["pending"] = True
        return {"ok": True, "message": f"偏移告警已登记（偏移量 {entry.get('偏移量', '0')}）"}

    def batch_run_pending(self) -> dict[str, Any]:
        """批量执行待核对批次。"""
        pending = [r for r in store.rows(self.MODULE) if r.get("status") == "待核对"]
        passed, alarmed = 0, 0
        for row in pending:
            result = self.run_check(int(row.get("id", 0)))
            if result.get("ok"):
                if "超阈值" in str(result.get("message", "")):
                    alarmed += 1
                else:
                    passed += 1
        return {"ok": True, "message": f"批量核对完成：通过 {passed}，告警 {alarmed}"}

    def consume_from_variant_assign(self, exp_code: str) -> dict[str, Any]:
        """读取分组分配器的分布统计，作为核对输入。"""
        rows = [r for r in store.rows("variant_assign") if r.get("实验编码") == exp_code]
        if not rows:
            return {"ok": False, "message": "实验无分组数据"}
        valid = [r for r in rows if r.get("status") != "已剔除"]
        return {
            "ok": True,
            "总分配数": len(rows),
            "有效分配数": len(valid),
            "实验组占比": round(sum(1 for r in valid if r.get("分组名称") == "实验组") / max(len(valid), 1), 4),
        }

    def consume_from_exp_design(self, exp_code: str) -> dict[str, Any]:
        """读取实验设计层流量比例作为计划比例。"""
        exp = next((r for r in store.rows("exp_design") if r.get("实验编码") == exp_code), None)
        if exp is None:
            return {"ok": False, "message": "实验方案不存在"}
        ratio = str(exp.get("流量比例", "0.5"))
        try:
            planned = float(ratio) / 100.0 if float(ratio) > 1 else float(ratio)
        except ValueError:
            planned = 0.5
        return {"ok": True, "计划比例": str(planned), "分组策略": exp.get("分组策略", "")}

    def export_offset_trend(self, exp_code: str) -> list[dict[str, Any]]:
        """导出实验偏移趋势，供报告生成器写入样本健康度章节。"""
        return [
            {
                "核对批次": r.get("核对批次", ""),
                "核对版次": r.get("核对版次", "R1"),
                "计划比例": r.get("计划比例", ""),
                "实际比例": r.get("实际比例", ""),
                "偏移量": r.get("偏移量", ""),
                "阈值": r.get("阈值", "0.05"),
            }
            for r in store.rows(self.MODULE) if r.get("实验编码") == exp_code
        ]
