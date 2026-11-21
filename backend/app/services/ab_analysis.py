"""实验分析领域服务：A/B指标计算、置信度、覆盖视图、异常检测联动。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store
from app.seed import AB_OBSERVATIONS
from app.analytics_engine import (
    compute_confidence_interval,
    compute_ab_sample_size,
    compute_coverage_dataset,
    compute_effect_with_coverage,
)


class AbAnalysisService(BaseModuleService):
    MODULE = "ab_analysis"
    ENTITY = "实验配置"
    OPERATION_TERM = "计算"
    LIST_FIELDS = ["实验名称", "对照组", "实验组", "核心指标", "置信度要求", "最小样本量", "实验周期"]
    REQUIRED_FIELDS = ["实验名称", "对照组", "实验组"]
    STATUS_ORDER = ["草稿", "进行中", "已结束", "已归档"]
    ACTION_RULES = {"开始实验": "进行中", "结束实验": "已结束", "归档实验": "已归档"}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        # 实验观测样本：{experiment_id: [{"period", "group", "value"}, ...]}
        self._observations: dict[int, list[dict[str, Any]]] = {
            exp_id: [dict(row) for row in rows]
            for exp_id, rows in AB_OBSERVATIONS.items()
        }

    # ---- 业务方法 ----

    def analyze(
        self, experiment_id: int, control_values: list[float],
        treatment_values: list[float],
    ) -> dict[str, Any]:
        """执行A/B实验分析，计算置信区间和显著性。"""
        experiment = store.find(self.MODULE, experiment_id)
        if experiment is None:
            return {"ok": False, "message": f"实验 {experiment_id} 不存在"}

        result = compute_confidence_interval(control_values, treatment_values)

        self.run_action(experiment_id, "开始实验")
        if result.get("significant"):
            self.run_action(experiment_id, "结束实验")

        return {
            "ok": True,
            "experiment_name": str(experiment.get("实验名称", "")),
            "metric": str(experiment.get("核心指标", "")),
            "control_size": len(control_values),
            "treatment_size": len(treatment_values),
            **result,
        }

    # ---- 覆盖视图与可比口径计算 ----

    def coverage_view(self, experiment_id: int) -> dict[str, Any]:
        """实验覆盖视图：从对照组和实验组样本计算实验覆盖数据集。

        按时段列出共同覆盖和仅单组有值的区间。
        """
        experiment = store.find(self.MODULE, experiment_id)
        if experiment is None:
            return {"ok": False, "message": f"实验 {experiment_id} 不存在"}
        dataset = self._coverage_dataset(experiment_id)
        return {
            "ok": True,
            "experiment_id": experiment_id,
            "experiment_name": str(experiment.get("实验名称", "")),
            "metric": str(experiment.get("核心指标", "")),
            **dataset,
        }

    def add_observations(
        self, experiment_id: int, group: str, period: str, values: list[float],
    ) -> dict[str, Any]:
        """补入观测记录（如下午的对照组），返回重算后的覆盖视图。"""
        experiment = store.find(self.MODULE, experiment_id)
        if experiment is None:
            return {"ok": False, "message": f"实验 {experiment_id} 不存在"}
        group = group.strip()
        if group not in ("对照组", "实验组"):
            return {"ok": False, "message": "组别只能是「对照组」或「实验组」"}
        period = period.strip()
        if not period:
            return {"ok": False, "message": "时段不能为空"}
        clean_values: list[float] = []
        for raw in values:
            try:
                clean_values.append(float(raw))
            except (TypeError, ValueError):
                return {"ok": False, "message": f"观测值 {raw!r} 不是数字"}
        if not clean_values:
            return {"ok": False, "message": "观测值不能为空"}

        bucket = self._observations.setdefault(experiment_id, [])
        bucket.extend(
            {"experiment_id": experiment_id, "period": period, "group": group, "value": v}
            for v in clean_values
        )
        return {
            "ok": True,
            "message": f"已补入{period}{group}观测 {len(clean_values)} 条",
            "added": len(clean_values),
            "coverage": self.coverage_view(experiment_id),
        }

    def analyze_with_coverage(self, experiment_id: int) -> dict[str, Any]:
        """按可比口径重算实验效果：仅共同覆盖区间进入结论。

        结果带采样覆盖和排除原因，并联动监控接口（同一批有效时段）。
        """
        experiment = store.find(self.MODULE, experiment_id)
        if experiment is None:
            return {"ok": False, "message": f"实验 {experiment_id} 不存在"}
        dataset = self._coverage_dataset(experiment_id)
        confidence = self._confidence_of(experiment)
        computed = compute_effect_with_coverage(dataset, confidence)
        monitoring = self.check_anomalies_during_experiment(
            experiment_id, coverage_dataset=dataset,
        )
        return {
            "ok": True,
            "experiment_id": experiment_id,
            "experiment_name": str(experiment.get("实验名称", "")),
            "metric": str(experiment.get("核心指标", "")),
            **computed,
            "monitoring": monitoring,
        }

    def _coverage_dataset(self, experiment_id: int) -> dict[str, Any]:
        observations = self._observations.get(experiment_id, [])
        control = [row for row in observations if row.get("group") == "对照组"]
        treatment = [row for row in observations if row.get("group") == "实验组"]
        return compute_coverage_dataset(control, treatment)

    @staticmethod
    def _confidence_of(experiment: dict[str, Any]) -> float:
        raw = str(experiment.get("置信度要求", "95%")).strip().rstrip("%")
        try:
            return float(raw) / 100
        except ValueError:
            return 0.95

    def estimate_required_sample(
        self, experiment_id: int, baseline_rate: float, min_effect: float,
    ) -> dict[str, Any]:
        """估算实验所需最小样本量。"""
        experiment = store.find(self.MODULE, experiment_id)
        if experiment is None:
            return {"ok": False, "message": f"实验 {experiment_id} 不存在"}
        required = compute_ab_sample_size(baseline_rate, min_effect)
        return {
            "ok": True,
            "experiment_name": experiment.get("实验名称"),
            "baseline_rate": baseline_rate,
            "min_detectable_effect": min_effect,
            "required_sample_size": required,
            "configured_min_sample": experiment.get("最小样本量"),
        }

    def check_anomalies_during_experiment(
        self, experiment_id: int, metric_values: list[float] | None = None,
        coverage_dataset: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """实验期间异常检测（实验分析→异常检测跨模块消费）。

        传入实验覆盖数据集时，监控接口把共同覆盖区间用于异常规则，
        判定和实验结果中的有效时段保持一致。
        """
        from app.services import service_for
        anomaly_svc = service_for("anomaly_detect")
        if anomaly_svc is None:
            return {"ok": False, "message": "异常检测服务不可用"}
        active_rules = anomaly_svc.get_active_rules()
        if not active_rules:
            return {"ok": True, "message": "无活跃检测规则", "anomalies": []}
        rule_ids = [int(r["id"]) for r in active_rules]
        if coverage_dataset is not None:
            return anomaly_svc.detect_with_coverage(rule_ids, coverage_dataset)
        batch_result = anomaly_svc.batch_detect(rule_ids, metric_values or [])
        return {
            "ok": True,
            "experiment_id": experiment_id,
            "anomaly_count": batch_result.get("total_anomalies", 0),
            "details": batch_result.get("results", []),
        }

    def get_running_experiments(self) -> list[dict[str, Any]]:
        """获取进行中的实验。"""
        return [
            row for row in store.rows(self.MODULE)
            if row.get("status") == "进行中"
        ]

    def domain_snapshot(self) -> dict[str, Any]:
        base = super().domain_snapshot()
        rows = store.rows(self.MODULE)
        base["artifact"] = "AB实验数据集"
        base["running_count"] = sum(1 for row in rows if row.get("status") == "进行中")
        base["completed_count"] = sum(1 for row in rows if row.get("status") == "已结束")
        return base
