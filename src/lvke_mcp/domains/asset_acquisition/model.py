"""Deterministic asset-acquisition and hotel/lease finance engine.

The module consumes ``finance_spec.v3`` only.  It deliberately keeps purchase
price, market rent, ADR, occupancy, leverage, interest, tenor, transaction tax,
maintenance capex, and exit value as independent scenario dimensions.

Wave 3.2 facade: implementation moved to ``_model/`` sub-modules — ``base``
(independent scenario fields, error type, number/series/IRR primitives),
``period_dates`` (date primitives; deliberately not named ``calendar`` so it
cannot be confused with the stdlib module these engines import), ``hotel_lease``
(hotel operation and lease portfolio), ``schedules`` (annual and monthly debt,
depreciation and lease-income schedules), ``monthly_engine`` (hotel monthly
model), ``solar_engine`` (solar annual model) and ``entry``
(``run_acquisition_model``, ``apply_scenario``, ``solve_max_acquisition_price``).

Pure move: the monthly hotel path and the annual solar path keep their own
engines, and no shared helper was merged across them.
"""

from __future__ import annotations

import copy  # noqa: F401
import calendar  # noqa: F401
import math  # noqa: F401
from datetime import date, datetime  # noqa: F401
from typing import Any  # noqa: F401

from lvke_mcp.domains.finance.calculations import irr, npv, payback_period  # noqa: F401

from lvke_mcp.domains.finance.spec import LATEST_SPEC_VERSION, validate  # noqa: F401

from ._model.balance_sheet import projection_consistency_ok, roll_annual_balance_sheet  # noqa: F401
from ._model.base import (  # noqa: F401
    INDEPENDENT_SCENARIO_FIELDS,
    AcquisitionModelError,
    _number,
    _safe_irr,
    _series,
)
from ._model.entry import (  # noqa: F401
    apply_scenario,
    run_acquisition_model,
    solve_max_acquisition_price,
)
from ._model.hotel_lease import (  # noqa: F401
    _contract_rent_for_year,
    _escalation_count,
    _lease_annual_base,
    calculate_hotel_operation,
    calculate_lease_portfolio,
)
from ._model.monthly_engine import _run_monthly_acquisition_model  # noqa: F401
from ._model.period_dates import (  # noqa: F401
    _add_months,
    _date,
    _date_value,
    _month_end,
    _month_overlap_days,
    _month_start,
)
from ._model.schedules import (  # noqa: F401
    _debt_schedule,
    _depreciation_schedule,
    _monthly_debt_schedule,
    _monthly_lease_income,
)
from ._model.solar_engine import _run_solar_acquisition_model  # noqa: F401
