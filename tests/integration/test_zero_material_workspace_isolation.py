"""零材料交付域的 workspace 隔离回归测试。

背景：`standard_resource_entries()` 曾挂在协议层 `register_resource_provider`
上并遍历 data root 下的目录。它当时因 `workspace_root(".").parent` 被 pathlib
规范化（退到 data_root 而非 workspaces/）而恒返回 0 条，泄露未真正发生；但一旦
有人"修正"该路径，全机 workspace 会立刻暴露。

协议层 lister 回调无参、拿不到 workspace 身份，因此**无法**按租户过滤。修复方式
是与其余 12 个 server 一致留空 lister，工作区内枚举只走带 workspace_id 的
`service.list_resources`。本测试锁住这个契约。
"""

from __future__ import annotations

import os
import tempfile
import unittest

from lvke_mcp.servers.lvke_zero_material_delivery import server as server_module
from lvke_mcp.servers.lvke_zero_material_delivery import service


class ZeroMaterialWorkspaceIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory(prefix="lvke-zmd-iso-")
        self.previous = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = self.tempdir.name
        self.alpha = "tenant-alpha"
        self.beta = "tenant-beta"
        for workspace in (self.alpha, self.beta):
            service.create_from_sentence({
                "workspace_id": workspace,
                "sentence": f"{workspace} 在湖北建设 50MW 光伏电站可研",
                "idempotency_key": f"iso-{workspace}",
            })

    def tearDown(self) -> None:
        if self.previous is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self.previous
        self.tempdir.cleanup()

    def _scoped_uris(self, workspace_id: str) -> list[str]:
        response = service.list_resources({"workspace_id": workspace_id})
        self.assertTrue(response.get("success"), response)
        return [str(item) for item in response.get("resource_uris") or []]

    def test_protocol_lister_never_enumerates_objects(self) -> None:
        """协议层 resources/list 必须恒空——它没有 workspace 身份可用于过滤。"""

        server = server_module.build_server()
        providers = server._resource_providers  # noqa: SLF001
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0].lister(), [])

    def test_scoped_listing_is_partitioned_by_workspace(self) -> None:
        alpha_uris = self._scoped_uris(self.alpha)
        beta_uris = self._scoped_uris(self.beta)

        self.assertTrue(alpha_uris, "alpha 应能看到自己的对象")
        self.assertTrue(beta_uris, "beta 应能看到自己的对象")
        for uri in alpha_uris:
            self.assertIn(f"/workspaces/{self.alpha}/", uri)
        for uri in beta_uris:
            self.assertIn(f"/workspaces/{self.beta}/", uri)
        self.assertFalse(set(alpha_uris) & set(beta_uris), "两个 workspace 不得有交集")

    def test_cross_workspace_read_is_rejected(self) -> None:
        alpha_uri = self._scoped_uris(self.alpha)[0]

        response = service.read_resource({"workspace_id": self.beta, "uri": alpha_uri})

        self.assertFalse(response.get("success"))
        self.assertEqual(response.get("code"), "resource_scope_mismatch")

    def test_own_workspace_read_still_succeeds(self) -> None:
        """隔离不能把正常读也挡掉。"""

        alpha_uri = self._scoped_uris(self.alpha)[0]

        response = service.read_resource({"workspace_id": self.alpha, "uri": alpha_uri})

        self.assertTrue(response.get("success"), response)


if __name__ == "__main__":
    unittest.main()
