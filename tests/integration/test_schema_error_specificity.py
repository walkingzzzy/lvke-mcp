from __future__ import annotations

import unittest

from jsonschema.exceptions import ValidationError

from lvke_mcp.runtime.transport import OfficialStdioServer
from lvke_mcp.servers.lvke_asset_acquisition.server import _SPEC_SCHEMA


class SchemaErrorSpecificityTests(unittest.TestCase):
    """校验失败必须报**最具体**的那条错误，而不是第一条。

    jsonschema 先报容器级校验器（oneOf/anyOf/allOf），对判别式联合类型其 message
    只有 "is not valid under any of the given schemas" —— 零信息量。收购 spec 的
    两个分支各 19KB/11KB，远超判别式能存活的 2KiB 上限（见 oneOf 分支上限约束），
    所以实测填错 asset_type 要逐分支二分才能定位。

    transport 此前取 ``next(iter_errors(...))``，恰好取到那条没用的。改一处惠及
    14 个服务的全部入口。
    """

    def _error(self, payload: dict, schema: dict) -> ValidationError:
        with self.assertRaises(ValidationError) as caught:
            OfficialStdioServer._validate(payload, schema)
        return caught.exception

    def test_bad_discriminant_names_the_field_and_legal_values(self) -> None:
        error = self._error(
            {"version": "finance_spec.v3", "asset_type": "wind", "transaction": {}},
            _SPEC_SCHEMA,
        )
        self.assertEqual(list(error.absolute_path), ["asset_type"])
        self.assertIn("hotel_lease", error.message)
        self.assertIn("solar_power", error.message)
        self.assertNotIn("is not valid under any of the given schemas", error.message)

    def test_valid_solar_spec_still_accepted(self) -> None:
        OfficialStdioServer._validate(
            {
                "version": "finance_spec.v3",
                "asset_type": "solar_power",
                "transaction": {"calculation_granularity": "annual"},
                "solar_operation": {"installed_capacity_mw": 10, "tariff_yuan_per_kwh": 0.42},
            },
            _SPEC_SCHEMA,
        )

    def test_omitted_asset_type_is_not_rejected_by_the_guard(self) -> None:
        """hotel 分支的 required 不含 asset_type（省略即 hotel_lease），
        判别式前置门只在显式提供时校验，不能变成无条件必填。"""

        error = self._error(
            {"version": "finance_spec.v3", "transaction": {}},
            _SPEC_SCHEMA,
        )
        # 报的是分支内字段问题，而不是 asset_type 缺失。
        self.assertNotEqual(list(error.absolute_path), ["asset_type"])

    def test_nested_field_error_still_points_at_that_field(self) -> None:
        error = self._error(
            {
                "version": "finance_spec.v3",
                "asset_type": "solar_power",
                "transaction": {"calculation_granularity": "annual"},
                "solar_operation": {"installed_capacity_mw": -5, "tariff_yuan_per_kwh": 0.42},
            },
            _SPEC_SCHEMA,
        )
        self.assertIn("installed_capacity_mw", list(error.absolute_path))


if __name__ == "__main__":
    unittest.main()
