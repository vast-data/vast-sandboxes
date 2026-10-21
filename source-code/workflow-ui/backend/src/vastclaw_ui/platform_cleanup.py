"""Full platform teardown: vastclaw-db tables + per-department Kubernetes resources. python -m vastclaw_ui.platform_cleanup."""

from __future__ import annotations

import logging
import sys

from vastclaw_ui.config import Settings
from vastclaw_ui.db_cleanup import wipe_vastclaw_db
from vastclaw_ui.runtime import KubernetesProvisioner

_log = logging.getLogger("vastclaw.platform_cleanup")


def departments_for_cleanup(settings: Settings) -> list[str]:
    depts: set[str] = set(settings.departments.keys())
    for user in settings.auth.users.values():
        if user.department and not user.is_admin:
            depts.add(user.department)
    return sorted(depts)


def wipe_kubernetes(settings: Settings) -> dict[str, dict[str, list[str]]]:
    prov = KubernetesProvisioner(settings.sandbox)
    result: dict[str, dict[str, list[str]]] = {}
    for dept in departments_for_cleanup(settings):
        try:
            purged = prov.purge_department(dept, agent_sandbox=settings.agent_sandbox)
            result[dept] = purged
            _log.info(
                "purged vastclaw-%s: %d sandbox(es), %d job(s), %d pvc(s)",
                dept,
                len(purged.get("sandboxes", [])),
                len(purged.get("jobs", [])),
                len(purged.get("pvcs", [])),
            )
        except Exception as exc:
            _log.warning("kubernetes purge for %s failed: %s", dept, exc)
            result[dept] = {"error": [str(exc)]}
    return result


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = Settings.from_yaml()
    failed = False

    try:
        wipe_vastclaw_db(settings)
    except Exception as exc:
        _log.error("vastclaw-db cleanup failed: %s", exc)
        failed = True

    for dept, purged in wipe_kubernetes(settings).items():
        if "error" in purged:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
