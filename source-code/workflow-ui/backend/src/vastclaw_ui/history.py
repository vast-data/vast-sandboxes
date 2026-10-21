from __future__ import annotations

from typing import Any, Protocol

from vastclaw_ui.config import VastCredentials, VastDBSettings
from vastclaw_ui.models import (
    AdminAction,
    ReportMeta,
    Run,
    Workflow,
    WorkflowTurn,
)


class WorkflowNotFoundError(Exception):
    pass


class HistoryStore(Protocol):
    def ensure_schema(self) -> None: ...

    def create_workflow(self, workflow: Workflow) -> Workflow: ...

    def get_workflow(self, workflow_id: str, department: str | None) -> Workflow: ...

    def list_workflows(self, department: str | None) -> list[Workflow]: ...

    def add_turn(self, turn: WorkflowTurn) -> WorkflowTurn: ...

    def list_turns(self, workflow_id: str) -> list[WorkflowTurn]: ...

    def record_run(self, run: Run) -> Run: ...

    def next_seq(self, workflow_id: str) -> int: ...

    def update_workflow_status(self, workflow_id: str, status: str, updated_at: str) -> None: ...

    def set_last_run(self, workflow_id: str, run_id: str, updated_at: str) -> None: ...

    def update_turn_status(self, workflow_id: str, turn_id: str, status: str) -> None: ...

    def update_run_status(self, run_id: str, status: str, ended_at: str) -> None: ...

    def record_admin_action(self, action: AdminAction) -> AdminAction: ...

    def list_admin_actions(self, limit: int = 50) -> list[AdminAction]: ...

    def next_report_version(self, workflow_id: str) -> int: ...

    def save_report(self, meta: ReportMeta, pdf: bytes) -> ReportMeta: ...

    def list_reports(self, workflow_id: str) -> list[ReportMeta]: ...

    def get_report_pdf(self, workflow_id: str, report_id: str) -> bytes | None: ...

    def delete_workflow(self, workflow_id: str) -> None: ...

    def reset(self) -> None: ...


def _visible(workflow: Workflow, department: str | None) -> bool:
    return department is None or workflow.department == department


class InMemoryHistoryStore:
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._turns: dict[str, list[WorkflowTurn]] = {}
        self._runs: dict[str, Run] = {}
        self._audit: list[AdminAction] = []
        self._reports: dict[str, list[tuple[ReportMeta, bytes]]] = {}

    def ensure_schema(self) -> None:
        return None

    def create_workflow(self, workflow: Workflow) -> Workflow:
        self._workflows[workflow.workflow_id] = workflow
        self._turns.setdefault(workflow.workflow_id, [])
        return workflow

    def get_workflow(self, workflow_id: str, department: str | None) -> Workflow:
        workflow = self._workflows.get(workflow_id)
        if workflow is None or not _visible(workflow, department):
            raise WorkflowNotFoundError(workflow_id)
        return workflow

    def list_workflows(self, department: str | None) -> list[Workflow]:
        items = [w for w in self._workflows.values() if _visible(w, department)]
        return sorted(items, key=lambda w: w.created_at, reverse=True)

    def add_turn(self, turn: WorkflowTurn) -> WorkflowTurn:
        self._turns.setdefault(turn.workflow_id, []).append(turn)
        workflow = self._workflows.get(turn.workflow_id)
        if workflow is not None:
            workflow.updated_at = turn.created_at
        return turn

    def list_turns(self, workflow_id: str) -> list[WorkflowTurn]:
        return sorted(self._turns.get(workflow_id, []), key=lambda t: t.seq)

    def record_run(self, run: Run) -> Run:
        self._runs[run.run_id] = run
        return run

    def next_seq(self, workflow_id: str) -> int:
        return len(self._turns.get(workflow_id, [])) + 1

    def update_workflow_status(self, workflow_id: str, status: str, updated_at: str) -> None:
        workflow = self._workflows.get(workflow_id)
        if workflow is not None:
            workflow.status = status
            workflow.updated_at = updated_at

    def set_last_run(self, workflow_id: str, run_id: str, updated_at: str) -> None:
        workflow = self._workflows.get(workflow_id)
        if workflow is not None:
            workflow.last_run_id = run_id
            workflow.updated_at = updated_at

    def update_turn_status(self, workflow_id: str, turn_id: str, status: str) -> None:
        for turn in self._turns.get(workflow_id, []):
            if turn.turn_id == turn_id:
                turn.status = status

    def update_run_status(self, run_id: str, status: str, ended_at: str) -> None:
        run = self._runs.get(run_id)
        if run is not None:
            run.status = status
            run.ended_at = ended_at

    def record_admin_action(self, action: AdminAction) -> AdminAction:
        self._audit.append(action)
        return action

    def list_admin_actions(self, limit: int = 50) -> list[AdminAction]:
        return sorted(self._audit, key=lambda a: a.ts, reverse=True)[:limit]

    def next_report_version(self, workflow_id: str) -> int:
        return len(self._reports.get(workflow_id, [])) + 1

    def save_report(self, meta: ReportMeta, pdf: bytes) -> ReportMeta:
        self._reports.setdefault(meta.workflow_id, []).append((meta, pdf))
        return meta

    def list_reports(self, workflow_id: str) -> list[ReportMeta]:
        rows = [m for m, _ in self._reports.get(workflow_id, [])]
        return sorted(rows, key=lambda m: m.version, reverse=True)

    def get_report_pdf(self, workflow_id: str, report_id: str) -> bytes | None:
        for meta, pdf in self._reports.get(workflow_id, []):
            if meta.report_id == report_id:
                return pdf
        return None

    def delete_workflow(self, workflow_id: str) -> None:
        self._workflows.pop(workflow_id, None)
        self._turns.pop(workflow_id, None)
        self._reports.pop(workflow_id, None)
        self._runs = {
            rid: run for rid, run in self._runs.items() if run.workflow_id != workflow_id
        }

    def reset(self) -> None:
        # Factory reset: wipe workflow data, keep the admin audit log.
        self._workflows.clear()
        self._turns.clear()
        self._runs.clear()
        self._reports.clear()


class VastDBHistoryStore:
    def __init__(self, credentials: VastCredentials, settings: VastDBSettings) -> None:
        self._credentials = credentials
        self._settings = settings
        self._session: Any = None

    def _connect(self) -> Any:
        if self._session is None:
            import vastdb

            self._session = vastdb.connect(
                endpoint=self._settings.endpoint,
                access=self._credentials.access_key,
                secret=self._credentials.secret_key,
                ssl_verify=self._settings.ssl_verify,
            )
        return self._session

    def ensure_schema(self) -> None:
        """Idempotently create the control schema + tables this store reads/writes."""
        from vastclaw_ui.schema import bootstrap_control_tables

        bootstrap_control_tables(
            self._connect(), self._settings.control_bucket, self._settings.control_schema
        )

    def _table(self, tx: Any, name: str) -> Any:
        return (
            tx.bucket(self._settings.control_bucket)
            .schema(self._settings.control_schema)
            .table(name)
        )

    @staticmethod
    def _records(rows: list[dict[str, Any]]) -> Any:
        import pyarrow as pa

        return pa.Table.from_pylist(rows)

    def create_workflow(self, workflow: Workflow) -> Workflow:
        session = self._connect()
        with session.transaction() as tx:
            self._table(tx, "workflows").insert(self._records([workflow.model_dump()]))
        return workflow

    def get_workflow(self, workflow_id: str, department: str | None) -> Workflow:
        session = self._connect()
        with session.transaction() as tx:
            table = self._table(tx, "workflows")
            rows = (
                table.select(predicate=(table["workflow_id"] == workflow_id)).read_all().to_pylist()
            )
        if not rows:
            raise WorkflowNotFoundError(workflow_id)
        workflow = Workflow.model_validate(rows[0])
        if not _visible(workflow, department):
            raise WorkflowNotFoundError(workflow_id)
        return workflow

    def list_workflows(self, department: str | None) -> list[Workflow]:
        session = self._connect()
        with session.transaction() as tx:
            table = self._table(tx, "workflows")
            predicate = None if department is None else (table["department"] == department)
            rows = table.select(predicate=predicate).read_all().to_pylist()
        workflows = [Workflow.model_validate(row) for row in rows]
        return sorted(workflows, key=lambda w: w.created_at, reverse=True)

    def add_turn(self, turn: WorkflowTurn) -> WorkflowTurn:
        session = self._connect()
        with session.transaction() as tx:
            self._table(tx, "workflow_turns").insert(self._records([turn.model_dump()]))
        return turn

    def list_turns(self, workflow_id: str) -> list[WorkflowTurn]:
        session = self._connect()
        with session.transaction() as tx:
            table = self._table(tx, "workflow_turns")
            rows = (
                table.select(predicate=(table["workflow_id"] == workflow_id)).read_all().to_pylist()
            )
        turns = [WorkflowTurn.model_validate(row) for row in rows]
        return sorted(turns, key=lambda t: t.seq)

    def record_run(self, run: Run) -> Run:
        session = self._connect()
        with session.transaction() as tx:
            self._table(tx, "runs").insert(self._records([run.model_dump()]))
        return run

    def next_seq(self, workflow_id: str) -> int:
        return len(self.list_turns(workflow_id)) + 1

    def _update_rows(
        self, table_name: str, column: str, value: str, changes: dict[str, str]
    ) -> None:
        import pyarrow as pa

        session = self._connect()
        with session.transaction() as tx:
            table = self._table(tx, table_name)
            batch = table.select(
                predicate=(table[column] == value), internal_row_id=True
            ).read_all()
            if batch.num_rows == 0:
                return
            cols: dict[str, Any] = {"$row_id": batch.column("$row_id")}
            for key, val in changes.items():
                cols[key] = pa.array([val] * batch.num_rows)
            table.update(pa.table(cols), columns=list(changes.keys()))

    def update_workflow_status(self, workflow_id: str, status: str, updated_at: str) -> None:
        self._update_rows(
            "workflows", "workflow_id", workflow_id, {"status": status, "updated_at": updated_at}
        )

    def set_last_run(self, workflow_id: str, run_id: str, updated_at: str) -> None:
        self._update_rows(
            "workflows",
            "workflow_id",
            workflow_id,
            {"last_run_id": run_id, "updated_at": updated_at},
        )

    def update_turn_status(self, workflow_id: str, turn_id: str, status: str) -> None:
        self._update_rows("workflow_turns", "turn_id", turn_id, {"status": status})

    def update_run_status(self, run_id: str, status: str, ended_at: str) -> None:
        self._update_rows("runs", "run_id", run_id, {"status": status, "ended_at": ended_at})

    def record_admin_action(self, action: AdminAction) -> AdminAction:
        session = self._connect()
        with session.transaction() as tx:
            self._table(tx, "admin_audit").insert(self._records([action.model_dump()]))
        return action

    def list_admin_actions(self, limit: int = 50) -> list[AdminAction]:
        session = self._connect()
        with session.transaction() as tx:
            rows = self._table(tx, "admin_audit").select().read_all().to_pylist()
        actions = [AdminAction.model_validate(row) for row in rows]
        return sorted(actions, key=lambda a: a.ts, reverse=True)[:limit]

    def _delete_all(self, table_name: str) -> None:
        import pyarrow as pa

        session = self._connect()
        with session.transaction() as tx:
            schema = tx.bucket(self._settings.control_bucket).schema(
                self._settings.control_schema, fail_if_missing=False
            )
            table = None if schema is None else schema.table(table_name, fail_if_missing=False)
            if table is None:
                return
            batch = table.select(internal_row_id=True).read_all()
            if batch.num_rows:
                table.delete(pa.table({"$row_id": batch.column("$row_id")}))

    def _delete_where(self, table_name: str, column: str, value: str) -> None:
        import pyarrow as pa

        session = self._connect()
        with session.transaction() as tx:
            schema = tx.bucket(self._settings.control_bucket).schema(
                self._settings.control_schema, fail_if_missing=False
            )
            table = None if schema is None else schema.table(table_name, fail_if_missing=False)
            if table is None:
                return
            batch = table.select(
                predicate=(table[column] == value), internal_row_id=True
            ).read_all()
            if batch.num_rows:
                table.delete(pa.table({"$row_id": batch.column("$row_id")}))

    def delete_workflow(self, workflow_id: str) -> None:
        self._delete_where("workflows", "workflow_id", workflow_id)
        self._delete_where("workflow_turns", "workflow_id", workflow_id)
        self._delete_where("runs", "workflow_id", workflow_id)
        self._delete_where("reports", "workflow_id", workflow_id)

    def next_report_version(self, workflow_id: str) -> int:
        return len(self.list_reports(workflow_id)) + 1

    def save_report(self, meta: ReportMeta, pdf: bytes) -> ReportMeta:
        row = {**meta.model_dump(), "pdf": pdf}
        session = self._connect()
        with session.transaction() as tx:
            self._table(tx, "reports").insert(self._records([row]))
        return meta

    def list_reports(self, workflow_id: str) -> list[ReportMeta]:
        session = self._connect()
        with session.transaction() as tx:
            table = self._table(tx, "reports")
            # Exclude the (potentially large) pdf column from the listing.
            columns = list(ReportMeta.model_fields)
            rows = (
                table.select(predicate=(table["workflow_id"] == workflow_id), columns=columns)
                .read_all()
                .to_pylist()
            )
        reports = [ReportMeta.model_validate(row) for row in rows]
        return sorted(reports, key=lambda m: m.version, reverse=True)

    def get_report_pdf(self, workflow_id: str, report_id: str) -> bytes | None:
        session = self._connect()
        with session.transaction() as tx:
            table = self._table(tx, "reports")
            rows = (
                table.select(predicate=(table["report_id"] == report_id), columns=["pdf"])
                .read_all()
                .to_pylist()
            )
        if not rows:
            return None
        pdf = rows[0].get("pdf")
        return bytes(pdf) if pdf is not None else None

    def reset(self) -> None:
        for name in ("workflows", "workflow_turns", "runs", "reports"):
            self._delete_all(name)
