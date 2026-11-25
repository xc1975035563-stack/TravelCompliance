from threading import Lock


class Metrics:
    """进程内计数。进程重启后清零，见 README。"""

    def __init__(self):
        self._lock = Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.application_submitted = 0
            self.reimbursement_by_type = {"onsite": 0, "client_visit": 0, "training": 0}
            self.reimbursement_ms_total = 0.0
            self.reimbursement_count = 0
            self.rule_reject_count = 0
            self.three_way_mismatch_count = 0
            self.model_failure_count = 0

    def incr_application(self):
        with self._lock:
            self.application_submitted += 1

    def incr_model_failure(self):
        with self._lock:
            self.model_failure_count += 1

    def observe_reimbursement(self, trip_type: str, elapsed_ms: float, result: dict):
        with self._lock:
            self.reimbursement_count += 1
            self.reimbursement_ms_total += elapsed_ms
            if trip_type in self.reimbursement_by_type:
                self.reimbursement_by_type[trip_type] += 1
            if not result.get("ok") or result.get("refusal_code"):
                self.rule_reject_count += 1
            blob = str(result.get("refusal_code") or "") + " " + str(result)
            if "THREE_WAY" in blob or "AMOUNT_MISMATCH" in blob:
                self.three_way_mismatch_count += 1

    def incr_rule_reject(self):
        with self._lock:
            self.rule_reject_count += 1

    def incr_three_way(self):
        with self._lock:
            self.three_way_mismatch_count += 1

    def snapshot(self) -> dict:
        with self._lock:
            avg = 0.0
            if self.reimbursement_count:
                avg = self.reimbursement_ms_total / self.reimbursement_count
            return {
                "application_submitted": self.application_submitted,
                "reimbursement_by_type": dict(self.reimbursement_by_type),
                "reimbursement_count": self.reimbursement_count,
                "reimbursement_avg_ms": round(avg, 3),
                "rule_reject_count": self.rule_reject_count,
                "three_way_mismatch_count": self.three_way_mismatch_count,
                "model_failure_count": self.model_failure_count,
                "persistence": "in_process",
                "reset_on_restart": True,
            }


metrics = Metrics()
