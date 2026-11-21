"""异常检测领域服务：多算法检测、告警规则、AB实验消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app.analytics_engine import (
    detect_anomalies_zscore,
    detect_anomalies_iqr,
    detect_anomalies_moving_avg,
    common_coverage_series,
    coverage_exclusions,
)


class AnomalyDetectService(BaseModuleService):
    MODULE = "anomaly_detect"
    ENTITY = "检测规则"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["规则名称", "监控指标", "检测算法", "阈值设置", "参考周期", "告警级别", "是否启用"]
    REQUIRED_FIELDS = ["规则名称", "监控指标", "检测算法"]
    STATUS_ORDER = ["草稿", "监控中", "已触发", "已关闭"]
    ACTION_RULES = {"启用监控": "监控中", "确认触发": "已触发", "关闭规则": "已关闭"}
    NEGATIVE_ACTIONS = ["关闭规则"]

    # ---- 业务方法 ----

    def detect(
        self, rule_id: int, metric_values: list[float],
    ) -> dict[str, Any]:
        """对指标时间序列执行异常检测。"""
        rule = store.find(self.MODULE, rule_id)
        if rule is None:
            return {"ok": False, "message": f"检测规则 {rule_id} 不存在"}

        algorithm = str(rule.get("检测算法", "zscore")).lower()
        threshold_raw = str(rule.get("阈值设置", "3.0"))
        try:
            threshold = float(threshold_raw)
        except ValueError:
            threshold = 3.0

        anomalies: list[dict[str, Any]] = []
        if "iqr" in algorithm:
            anomalies = detect_anomalies_iqr(metric_values, threshold)
        elif "moving" in algorithm or "avg" in algorithm:
            window = int(rule.get("参考周期", "7"))
            anomalies = detect_anomalies_moving_avg(metric_values, window, threshold)
        else:
            anomalies = detect_anomalies_zscore(metric_values, threshold)

        self.run_action(rule_id, "启用监控")
        if anomalies:
            self.run_action(rule_id, "确认触发")

        alert_level = str(rule.get("告警级别", "低"))
        return {
            "ok": True,
            "rule_name": str(rule.get("规则名称", "")),
            "monitoring_metric": str(rule.get("监控指标", "")),
            "algorithm": algorithm,
            "data_points": len(metric_values),
            "anomaly_count": len(anomalies),
            "alert_level": alert_level,
            "anomalies": anomalies,
        }

    def batch_detect(
        self, rule_ids: list[int], metric_values: list[float],
    ) -> dict[str, Any]:
        """批量检测：多规则并行扫描同一指标序列。"""
        results: list[dict[str, Any]] = []
        for rid in rule_ids:
            r = self.detect(rid, metric_values)
            if r.get("ok"):
                results.append(r)
        total_anomalies = sum(r.get("anomaly_count", 0) for r in results)
        return {
            "ok": True,
            "rule_count": len(rule_ids),
            "total_anomalies": total_anomalies,
            "results": results,
        }

    def get_active_rules(self) -> list[dict[str, Any]]:
        """获取所有激活的检测规则（供实验分析消费）。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "监控中"
        ]

    def detect_with_coverage(
        self, rule_ids: list[int], coverage_dataset: dict[str, Any],
    ) -> dict[str, Any]:
        """监控接口：接收实验覆盖数据集，把共同覆盖区间用于异常规则。

        只对共同覆盖区间内的样本执行检测，判定用的有效时段与实验结果
        保持一致；区间外的样本（如缺对照组时段的尖峰）记录排除原因，不参与判定。
        """
        series = common_coverage_series(coverage_dataset)
        values = [point["value"] for point in series]
        valid_periods = list(coverage_dataset.get("common_periods", []))
        exclusions = coverage_exclusions(coverage_dataset)

        results: list[dict[str, Any]] = []
        for rid in rule_ids:
            rule = store.find(self.MODULE, rid)
            if rule is None:
                continue
            report = self.detect(rid, values)
            if not report.get("ok"):
                continue
            # 把异常下标映射回时段和组别，保证结论可核对到具体区间
            for anomaly in report.get("anomalies", []):
                idx = anomaly.get("index")
                if isinstance(idx, int) and 0 <= idx < len(series):
                    anomaly["period"] = series[idx]["period"]
                    anomaly["group"] = series[idx]["group"]
            report["valid_periods"] = valid_periods
            results.append(report)

        return {
            "ok": True,
            "dataset": "实验覆盖数据集",
            "valid_periods": valid_periods,
            "sampling_coverage": {
                "common_periods": valid_periods,
                "included_count": len(values),
            },
            "exclusions": exclusions,
            "rule_count": len(rule_ids),
            "total_anomalies": sum(r.get("anomaly_count", 0) for r in results),
            "results": results,
        }

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["artifact"] = "异常检测数据集"
        base["monitoring_count"] = sum(1 for row in rows if row.get("status") == "监控中")
        base["triggered_count"] = sum(1 for row in rows if row.get("status") == "已触发")
        return base