"""Deterministic finance checks shared by run, package, and combined reviews.

Wave 2.9 facade: implementation moved to ``_financial_checks/`` sub-modules —
``base`` (rule registry, number/tolerance primitives, source basis and the
``_finding`` constructor), ``generic_statements`` (period, depreciation and
working-capital groups), ``generic_debt``, ``generic_tax_source`` (sensitivity,
tax basis and finance source binding), ``acquisition`` (asset-acquisition rules)
and ``entry`` (``review_finance_run``).

The grouping follows the existing call order inside ``review_finance_run``, so
finding IDs, severity and blocker aggregation order are unchanged.
"""

from __future__ import annotations

from copy import deepcopy  # noqa: F401
from pathlib import Path  # noqa: F401
from typing import Any, Iterable  # noqa: F401

from lvke_mcp.servers.lvke_deliverable_review import rules  # noqa: F401

from ._financial_checks.acquisition import _acquisition_checks  # noqa: F401
from ._financial_checks.base import (  # noqa: F401
    BUILTIN_RULES,
    _different,
    _finding,
    _minimum_capital_pct,
    _number,
    _source_basis,
    _tolerance,
)
from ._financial_checks.entry import review_finance_run  # noqa: F401
from ._financial_checks.generic_debt import _generic_debt_checks  # noqa: F401
from ._financial_checks.generic_statements import (  # noqa: F401
    _generic_depreciation_checks,
    _generic_period_checks,
    _generic_working_capital_checks,
)
from ._financial_checks.generic_tax_source import (  # noqa: F401
    _generic_finance_source_checks,
    _generic_sensitivity_checks,
    _generic_tax_and_source_checks,
)
