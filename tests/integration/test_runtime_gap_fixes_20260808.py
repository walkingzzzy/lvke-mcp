"""2026-08-08 运行时缺口修复的回归门禁。

覆盖此前零测试覆盖的五条修复：

1. PDF 文本层解析 → page locator；扫描版诚实报 needs_ocr 而非 succeeded+空输出
2. SSRF 门对代理 fake-ip 段的可诊断性，以及两个门判定一致
3. provider_status 不把未探测能力和上游故障说成可用/配置缺口
4. data_search 的 ok 语义：部分低相关不再否定整次调用
5. planning_validate 支持 policy_basis

这些断言都对着「诚实性」而非「实现细节」：状态位必须与真实产出一致。
"""

from __future__ import annotations

import asyncio
import base64
import os
import socket
import tempfile
import unittest
import zlib
from typing import Any


def _minimal_text_pdf(pages: list[str]) -> bytes:
    """Build a tiny valid PDF with a real text layer (no external fixture)."""

    objects: list[bytes] = []
    page_ids = [4 + index * 2 for index in range(len(pages))]
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for index, text in enumerate(pages):
        pid = page_ids[index]
        objects.append(
            (
                "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 3 0 R >> >> "
                f"/MediaBox [0 0 200 200] /Contents {pid + 1} 0 R >>"
            ).encode()
        )
        escaped = text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 10 100 Td ({escaped}) Tj ET".encode()
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def _scanned_pdf() -> bytes:
    """A valid PDF whose only content is an image — i.e. no text layer."""

    raw = bytes([255, 0, 0] * 4)
    data = zlib.compress(raw)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /Resources << /XObject << /Im0 5 0 R >> >> "
            b"/MediaBox [0 0 200 200] /Contents 4 0 R >>"
        ),
        b"<< /Length 44 >>\nstream\nq 200 0 0 200 0 0 cm /Im0 Do Q\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 2 /Height 2 /ColorSpace /DeviceRGB "
        b"/BitsPerComponent 8 /Filter /FlateDecode /Length "
        + str(len(data)).encode()
        + b" >>\nstream\n"
        + data
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


class PdfParseGapTest(unittest.TestCase):
    """PDF 解析缺口：此前 succeeded + text_preview='' + 无 locators。"""

    def setUp(self) -> None:
        self._prev = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = tempfile.mkdtemp(prefix="lvke_pdftest_")
        os.environ["LVKE_MCP_TRANSPORT"] = "stdio"

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self._prev

    def _import(self, data: bytes, key: str) -> dict[str, Any]:
        from lvke_mcp.servers.lvke_source_files import service as svc

        result = svc.import_content(
            workspace_id="pdfws",
            original_filename="doc.pdf",
            declared_mime="application/pdf",
            content_base64=base64.b64encode(data).decode(),
            idempotency_key=key,
            parse_immediately=True,
        )
        return svc.get_source_file(workspace_id="pdfws", file_id=result["file_id"])

    def test_text_layer_pdf_yields_page_locators(self) -> None:
        detail = self._import(
            _minimal_text_pdf(["First page total 123", "Second page ratio 45"]), "k-text"
        )
        analysis = detail["analysis"]
        self.assertEqual(detail["source_file"]["extract_status"], "succeeded")
        self.assertEqual(detail["source_file"]["ocr_status"], "not_required")
        locators = analysis["locators"]
        self.assertEqual(len(locators), 2, "两页都应产出 locator")
        self.assertEqual([item["page"] for item in locators], [1, 2])
        self.assertTrue(all(item["kind"] == "pdf_page" for item in locators))
        self.assertIn("123", locators[0]["text"])
        self.assertIn("45", locators[1]["text"])
        # offsets 必须能把 locator 定位回拼接文本，否则引用不可追溯。
        self.assertEqual(locators[0]["start_offset"], 0)
        self.assertLess(locators[0]["end_offset"], locators[1]["start_offset"])
        self.assertTrue(analysis["text_preview"].strip())

    def test_scanned_pdf_is_partial_not_silently_succeeded(self) -> None:
        detail = self._import(_scanned_pdf(), "k-scan")
        record = detail["source_file"]
        # 关键诚实性断言：无文本层时绝不能报 succeeded。
        self.assertNotEqual(record["extract_status"], "succeeded")
        self.assertEqual(record["extract_status"], "partial")
        self.assertEqual(record["ocr_status"], "needs_ocr")
        self.assertEqual(record["degraded_reason"], "pdf_no_text_layer")
        self.assertEqual(detail["analysis"]["locators"], [])

    def test_scanned_pdf_is_not_formally_usable_downstream(self) -> None:
        from lvke_mcp.servers.lvke_data_analysis._service.ingest import _file_document

        detail = self._import(_scanned_pdf(), "k-scan2")
        document = _file_document("pdfws", detail["source_file"]["file_id"])
        assert document is not None
        # 旧行为：空文档却 formal_use_allowed=True。
        self.assertFalse(
            document["formal_use_allowed"],
            "无文本层的空文档不得被标记为可正式使用",
        )

    def test_text_pdf_is_ingestible_and_extractable(self) -> None:
        from lvke_mcp.servers.lvke_data_analysis import service as analysis

        detail = self._import(_minimal_text_pdf(["Occupancy rate 62 percent"]), "k-e2e")
        file_id = detail["source_file"]["file_id"]
        task = analysis.ingest(
            workspace_id="pdfws", source_snapshot_ids=[], file_ids=[file_id]
        )
        self.assertEqual(task["status"], "ok")
        document_locators = detail["analysis"]["locators"]
        self.assertTrue(document_locators)


class ProxyFakeIpDiagnosabilityTest(unittest.TestCase):
    """198.18.0.0/15 仍然拦截，但必须说清根因并给可行替代路径。"""

    def setUp(self) -> None:
        self._real = socket.getaddrinfo
        os.environ.pop("DR_ALLOW_PROXY_DNS", None)

    def tearDown(self) -> None:
        socket.getaddrinfo = self._real
        os.environ.pop("DR_ALLOW_PROXY_DNS", None)

    def _resolve_to(self, address: str) -> None:
        socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", (address, 0))]

    def test_fake_ip_blocked_with_root_cause_and_remediation(self) -> None:
        from lvke_mcp.domains.research.url_safety import url_safety_decision

        self._resolve_to("198.18.0.144")
        decision = url_safety_decision("https://www.gov.cn/")
        self.assertFalse(decision["allowed"], "仍必须拦截，不因可诊断性而放行")
        self.assertEqual(decision["code"], "proxy_fake_ip_resolution")
        self.assertEqual(
            [item["classification"] for item in decision["addresses"]],
            ["proxy_fake_ip"],
        )
        self.assertIn("198.18.0.144", str(decision["detail"]))
        remediation = decision["remediation"]
        self.assertTrue(
            any("tavily" in step for step in remediation),
            "必须指出受信提取这条真实可用路径",
        )

    def test_real_private_ip_keeps_private_classification(self) -> None:
        from lvke_mcp.domains.research.url_safety import url_safety_decision

        self._resolve_to("192.168.1.5")
        decision = url_safety_decision("https://internal.example/")
        self.assertEqual(decision["code"], "private_or_internal_resolution")
        self.assertEqual(
            [item["classification"] for item in decision["addresses"]], ["private"]
        )

    def test_cloud_metadata_self_reports_and_stays_blocked(self) -> None:
        from lvke_mcp.domains.research.url_safety import url_safety_decision

        self._resolve_to("169.254.169.254")
        decision = url_safety_decision("https://x.example/")
        self.assertFalse(decision["allowed"])
        self.assertEqual(decision["code"], "cloud_metadata_resolution")

    def test_both_gates_agree_by_default(self) -> None:
        """历史缺陷 MCP-P2-005：两个门对同一地址给相反结论。"""

        from lvke_mcp.domains.research.extractor import _public_address
        from lvke_mcp.domains.research.url_safety import _ip_classification
        import ipaddress

        address = "198.18.0.144"
        url_safety_allows = (
            _ip_classification(ipaddress.ip_address(address)) == "public"
        )
        self.assertFalse(url_safety_allows)
        self.assertFalse(
            _public_address(address), "默认必须与 url_safety 一致地拒绝"
        )

    def test_proxy_dns_opt_in_is_explicit(self) -> None:
        from lvke_mcp.domains.research.extractor import _public_address

        os.environ["DR_ALLOW_PROXY_DNS"] = "1"
        self.assertTrue(_public_address("198.18.0.144"))

    def test_fetch_layer_surfaces_detail_and_actions(self) -> None:
        from lvke_mcp.servers.lvke_data_acquisition._service.snapshots import (
            _blocked_next_actions,
            _network_safety_decision,
        )

        self._resolve_to("198.18.0.144")
        message, decision = asyncio.run(
            _network_safety_decision("https://www.gov.cn/")
        )
        self.assertIsNotNone(message)
        self.assertIn("198.18.0.144", str(message))
        actions = _blocked_next_actions(
            [{"status": "blocked", "security_decision": decision}]
        )
        self.assertTrue(any("tavily" in step for step in actions))
        self.assertNotIn(
            "移除 URL 中携带的密钥或改用公网可达地址后重试",
            actions,
            "fake-ip 场景不应给出误导性的通用建议",
        )

    def test_audit_reason_code_matches_fetch_diagnosis(self) -> None:
        from lvke_mcp.servers.lvke_data_acquisition._service.audit_capture import (
            _url_safety_diagnosis,
        )

        self._resolve_to("198.18.0.144")
        self.assertEqual(
            _url_safety_diagnosis("https://www.gov.cn/")["reason_code"],
            "proxy_fake_ip_resolution",
        )


class ProviderStatusHonestyTest(unittest.TestCase):
    """provider_status 不得把未探测能力或上游故障说成配置结论。"""

    def test_unprobed_extract_is_null_not_true(self) -> None:
        from lvke_mcp.domains.research.providers import tavily

        async def _fake_call(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {"results": []}

        original_call = tavily._call_tool
        original_transport = tavily.configured_transport
        tavily._call_tool = _fake_call
        tavily.configured_transport = lambda: "streamable_http"
        try:
            status = asyncio.run(tavily.provider_status())
        finally:
            tavily._call_tool = original_call
            tavily.configured_transport = original_transport
        self.assertTrue(status["search"])
        self.assertIsNone(status["extract"], "extract 从未被探测，不能声明 True")
        self.assertEqual(status["capabilities"]["probe"]["extract"], "not_probed")

    def test_upstream_failure_is_not_reported_as_config_gap(self) -> None:
        from lvke_mcp.domains.research.providers import tavily
        from lvke_mcp.servers.lvke_data_acquisition._service import resources

        async def _boom(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError("upstream 554")

        original_call = tavily._call_tool
        original_transport = tavily.configured_transport
        tavily._call_tool = _boom
        tavily.configured_transport = lambda: "streamable_http"
        try:
            envelope = asyncio.run(resources.provider_status())
        finally:
            tavily._call_tool = original_call
            tavily.configured_transport = original_transport
        self.assertEqual(envelope["status"], "blocked")
        self.assertIn("provider_upstream_unavailable", envelope["blockers"])
        self.assertNotIn(
            "provider_configuration_missing",
            envelope["blockers"],
            "传输已配置时不得把上游故障归因为本地配置缺口",
        )

    def test_missing_receipt_secret_is_warned_before_fetch_blocks(self) -> None:
        from lvke_mcp.domains.research.providers import tavily
        from lvke_mcp.servers.lvke_data_acquisition._service import resources

        async def _ok(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {"results": []}

        original_call = tavily._call_tool
        original_transport = tavily.configured_transport
        prev_secret = os.environ.pop("LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET", None)
        tavily._call_tool = _ok
        tavily.configured_transport = lambda: "streamable_http"
        try:
            envelope = asyncio.run(resources.provider_status())
        finally:
            tavily._call_tool = original_call
            tavily.configured_transport = original_transport
            if prev_secret is not None:
                os.environ["LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET"] = prev_secret
        self.assertEqual(envelope["status"], "ok")
        self.assertTrue(
            any("RECEIPT_SECRET" in warning for warning in envelope["warnings"]),
            "前置检查必须能发现会让 data_fetch 每次 blocked 的配置缺口",
        )
        self.assertEqual(envelope["probe_coverage"]["extract"], "not_probed")


class ReceiptSecretFileFallbackTest(unittest.TestCase):
    """密钥可用 *_FILE 间接持有，供分发用 .mcp.json 引用路径而不内嵌密钥。"""

    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in (
                "LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET",
                "LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET_FILE",
            )
        }
        for key in self._saved:
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_both_unset_returns_empty(self) -> None:
        from lvke_mcp.runtime.config import external_receipt_secret

        self.assertEqual(external_receipt_secret(), b"")

    def test_secret_file_is_read_and_stripped(self) -> None:
        from pathlib import Path

        from lvke_mcp.runtime.config import external_receipt_secret

        path = Path(tempfile.mkdtemp()) / "secret"
        path.write_text("  file-held-secret\n", encoding="utf-8")
        os.environ["LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET_FILE"] = str(path)
        self.assertEqual(external_receipt_secret(), b"file-held-secret")

    def test_direct_env_wins_over_file(self) -> None:
        from pathlib import Path

        from lvke_mcp.runtime.config import external_receipt_secret

        path = Path(tempfile.mkdtemp()) / "secret"
        path.write_text("from-file", encoding="utf-8")
        os.environ["LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET_FILE"] = str(path)
        os.environ["LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET"] = "from-env"
        self.assertEqual(external_receipt_secret(), b"from-env")

    def test_unreadable_file_degrades_without_raising(self) -> None:
        from lvke_mcp.runtime.config import external_receipt_secret

        os.environ["LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET_FILE"] = "/nonexistent/x"
        self.assertEqual(external_receipt_secret(), b"")

    def test_provider_status_sees_file_held_secret(self) -> None:
        """前置检查必须与签发能力一致：*_FILE 部署不得被误报未配置。"""

        from pathlib import Path

        from lvke_mcp.domains.research.providers import tavily

        path = Path(tempfile.mkdtemp()) / "secret"
        path.write_text("file-held", encoding="utf-8")
        os.environ["LVKE_EXTERNAL_EXTRACT_RECEIPT_SECRET_FILE"] = str(path)

        async def _ok(*_a: Any, **_k: Any) -> dict[str, Any]:
            return {"results": []}

        original_call = tavily._call_tool
        original_transport = tavily.configured_transport
        tavily._call_tool = _ok
        tavily.configured_transport = lambda: "streamable_http"
        try:
            status = asyncio.run(tavily.provider_status())
        finally:
            tavily._call_tool = original_call
            tavily.configured_transport = original_transport
        self.assertTrue(status["receipt_secret_configured"])
        self.assertTrue(status["formal_extract_ready"])


class TrustedHostReachesFetchPathTest(unittest.TestCase):
    """白名单必须作用到真正发请求的那一层（Gate B），否则「可固化却抓不到」。

    回归背景：把 Gate B 默认改成拒绝 fake-ip 后，白名单只加在 Gate A
    (url_safety)，direct_http 仍报 "URL is not an allowed public HTTP(S) target"。
    """

    def setUp(self) -> None:
        self._saved = os.environ.get("LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS")
        os.environ["LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS"] = (
            "www.whxinzhou.gov.cn,evil.example"
        )
        os.environ.pop("DR_ALLOW_PROXY_DNS", None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS", None)
        else:
            os.environ["LVKE_MCP_TRUSTED_HTTPS_PRIVATE_IP_HOSTS"] = self._saved

    @staticmethod
    def _resolver(addresses: list[str]):
        return lambda _host, _port: addresses

    def _both_gates(self, url: str, addresses: list[str]) -> tuple[bool, bool]:
        from lvke_mcp.domains.research.extractor import (
            _resolve_public_target,
            _safe_public_url,
        )

        resolver = self._resolver(addresses)
        safe = _safe_public_url(url, resolver=resolver)
        try:
            _resolve_public_target(url, resolver=resolver)
            resolved = True
        except Exception:  # noqa: BLE001 - UnsafePublicURLError 或其它均视为拒绝
            resolved = False
        return safe, resolved

    def test_allowlisted_https_fake_ip_is_fetchable(self) -> None:
        safe, resolved = self._both_gates(
            "https://www.whxinzhou.gov.cn/tjgb.pdf", ["198.18.0.144"]
        )
        self.assertTrue(safe, "前置门不得拒掉白名单域名")
        self.assertTrue(resolved, "解析层必须放行白名单域名，否则抓不到")

    def test_allowlisted_http_is_still_blocked(self) -> None:
        safe, resolved = self._both_gates(
            "http://www.whxinzhou.gov.cn/tjgb.pdf", ["198.18.0.144"]
        )
        self.assertFalse(safe, "白名单仅 HTTPS 生效")
        self.assertFalse(resolved)

    def test_non_allowlisted_fake_ip_still_blocked(self) -> None:
        safe, resolved = self._both_gates("https://other.example/x", ["198.18.0.144"])
        self.assertFalse(safe)
        self.assertFalse(resolved)

    def test_allowlist_cannot_bypass_cloud_metadata(self) -> None:
        """SSRF 关键边界：主机名白名单绝不能放行 metadata 端点。"""

        for address in ("169.254.169.254", "169.254.170.2", "100.100.100.200"):
            with self.subTest(address=address):
                safe, resolved = self._both_gates("https://evil.example/", [address])
                self.assertFalse(safe, f"{address} 必须无条件拒绝")
                self.assertFalse(resolved, f"{address} 必须无条件拒绝")

    def test_ordinary_public_address_unaffected(self) -> None:
        safe, resolved = self._both_gates("https://example.com/", ["93.184.216.34"])
        self.assertTrue(safe)
        self.assertTrue(resolved)

    def test_peer_mismatch_still_rejected_for_allowlisted_host(self) -> None:
        """白名单放宽的是「非公网」判定，不是「peer 必须等于预解析地址」。"""

        from lvke_mcp.domains.research.extractor import (
            UnsafePublicURLError,
            _validate_connected_peer,
        )

        class _Sock:
            @staticmethod
            def getpeername() -> tuple[str, int]:
                return ("198.18.0.199", 443)  # 与 expected 不同

        with self.assertRaises(UnsafePublicURLError):
            _validate_connected_peer(
                _Sock(), "198.18.0.144", trusted_private_host=True
            )

    def test_metadata_peer_rejected_even_when_trusted(self) -> None:
        from lvke_mcp.domains.research.extractor import (
            UnsafePublicURLError,
            _validate_connected_peer,
        )

        class _Sock:
            @staticmethod
            def getpeername() -> tuple[str, int]:
                return ("169.254.169.254", 443)

        with self.assertRaises(UnsafePublicURLError):
            _validate_connected_peer(
                _Sock(), "169.254.169.254", trusted_private_host=True
            )


class SearchStatusSemanticsTest(unittest.TestCase):
    """部分低相关结果不应否定整次调用。"""

    def test_partial_low_relevance_still_ok_with_warning(self) -> None:
        from lvke_mcp.servers.lvke_data_acquisition._service import searching

        results = [
            {"relevance": 1.0},
            {"relevance": 0.86},
            {"relevance": 0.2},
        ]
        relevant = sum(
            item["relevance"] >= searching._SEARCH_RELEVANCE_THRESHOLD
            for item in results
        )
        self.assertEqual(relevant, 2)
        # 复刻服务里的状态判定，锁住语义而不重跑网络。
        warnings: list[str] = []
        if not results:
            status = "empty"
        elif relevant == 0:
            status = "partial"
        else:
            if relevant < len(results):
                warnings.append("search_results_include_low_relevance")
            status = "ok"
        self.assertEqual(status, "ok")
        self.assertIn("search_results_include_low_relevance", warnings)

    def test_all_below_threshold_remains_partial(self) -> None:
        from lvke_mcp.servers.lvke_data_acquisition._service import searching

        results = [{"relevance": 0.1}, {"relevance": 0.2}]
        relevant = sum(
            item["relevance"] >= searching._SEARCH_RELEVANCE_THRESHOLD
            for item in results
        )
        self.assertEqual(relevant, 0)

    def test_discover_only_degrades_on_real_query_failure(self) -> None:
        """data_search 的 partial 不应再连坐 discover 的 business_success。"""

        for statuses, expected in (
            ([{"status": "ok"}, {"status": "partial"}], False),
            ([{"status": "ok"}, {"status": "empty"}], True),
            ([{"status": "upstream_failure"}], True),
        ):
            with self.subTest(statuses=statuses):
                degraded = any(
                    item.get("status") in {"upstream_failure", "empty"}
                    for item in statuses
                )
                self.assertEqual(degraded, expected)


class PolicyBasisValidateTest(unittest.TestCase):
    """planning_validate 补上 policy_basis 分支。"""

    def setUp(self) -> None:
        self._prev = os.environ.get("LVKE_MCP_DATA_DIR")
        os.environ["LVKE_MCP_DATA_DIR"] = tempfile.mkdtemp(prefix="lvke_pbtest_")

    def tearDown(self) -> None:
        if self._prev is None:
            os.environ.pop("LVKE_MCP_DATA_DIR", None)
        else:
            os.environ["LVKE_MCP_DATA_DIR"] = self._prev

    def _seed(self) -> tuple[str, str]:
        from lvke_mcp.domains.project_planning import application as app
        from lvke_mcp.servers.lvke_project_planning import lifecycle as lc

        context = app.create_project_context(
            "pbws",
            {
                "project_name": "测试项目", "industry_code": "E48",
                "project_type": "new_build", "objective": "验证",
                "report_type": "feasibility_study", "target_type": "project",
                "region": {"province": "湖北省"},
            },
            idempotency_key="ctx",
        )
        candidates = [
            {
                "candidate_id": "p1", "title": "适用政策",
                "classification": "applicable", "source_snapshot_id": "src_a",
                "content_hash": "sha256:" + "a" * 64, "locator": "第三条",
            },
            {
                "candidate_id": "p2", "title": "过期政策",
                "classification": "expired", "source_snapshot_id": "src_b",
                "content_hash": "sha256:" + "b" * 64, "locator": "第一条",
            },
        ]
        prepared = lc.prepare_policy_basis(
            "pbws",
            context["project_context"]["project_context_id"],
            candidates,
            idempotency_key="prep",
        )
        return prepared["policy_basis"]["policy_basis_id"], "pbws"

    def test_enum_now_includes_policy_basis(self) -> None:
        from lvke_mcp.servers.lvke_project_planning._server.dispatch_tables import (
            _VALIDATE_BRANCHES,
        )

        self.assertIn("policy_basis", _VALIDATE_BRANCHES)

    def test_candidate_object_warns_not_confirmed(self) -> None:
        from lvke_mcp.servers.lvke_project_planning import lifecycle as lc

        policy_basis_id, workspace = self._seed()
        result = lc.validate_policy_basis(workspace, policy_basis_id)
        self.assertEqual(result["status"], "ok")
        self.assertIn("policy_basis_not_confirmed", result["warnings"])

    def test_confirmed_object_validates_clean(self) -> None:
        from lvke_mcp.servers.lvke_project_planning import lifecycle as lc

        policy_basis_id, workspace = self._seed()
        confirmed = lc.confirm_policy_basis(
            workspace, policy_basis_id, ["p1"],
            "该政策直接适用于本项目用地性质", idempotency_key="conf",
        )
        result = lc.validate_policy_basis(
            workspace, confirmed["policy_basis"]["policy_basis_id"]
        )
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["valid"])
        self.assertEqual(result["policy_basis_status"], "confirmed")
        self.assertEqual(result["warnings"], [])

    def test_missing_object_is_blocked_not_crash(self) -> None:
        from lvke_mcp.servers.lvke_project_planning import lifecycle as lc

        _policy_basis_id, workspace = self._seed()
        result = lc.validate_policy_basis(workspace, "policy_missing")
        self.assertEqual(result["code"], "policy_basis_not_found")


if __name__ == "__main__":
    unittest.main()
