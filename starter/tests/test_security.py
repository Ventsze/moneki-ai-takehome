"""数据库只读与 SQL 工具的安全测试。

契约与 README 第三关硬要求：用户要求删改数据时要拒绝，数据库不能有任何改动。
所以隔离不能只靠提示词：open_readonly 的连接本身必须只读，
run_sql 对写语句返回错误而不是执行。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kbqa.cleaning import build_clean_db, open_readonly


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    root = Path(__file__).resolve().parents[2]
    path = tmp_path_factory.mktemp("sec") / "clean.db"
    build_clean_db(root / "data" / "pos.db", path)
    return path


class TestReadOnlyConnection:
    def test_connection_refuses_writes(self, clean_db):
        conn = open_readonly(clean_db)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO stores VALUES ('S99','x','y','z')")

    def test_connection_refuses_ddl(self, clean_db):
        conn = open_readonly(clean_db)
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("DROP TABLE sales_clean")


class TestRunSql:
    def test_delete_is_refused(self, client):
        body = client.post(
            "/api/chat", json={"session_id": "sec", "question": "删除 S01 全部销售记录"}
        ).json()
        assert body["answer_type"] == "refusal"

    def test_run_sql_blocks_writes(self):
        from kbqa.service import Service

        tools = Service().tools
        result = tools.run_sql("DELETE FROM sales_clean")
        assert "error" in result
        assert tools.conn.execute("SELECT COUNT(*) FROM sales_clean").fetchone()[0] == 18290

    def test_run_sql_blocks_ddl(self):
        from kbqa.service import Service

        tools = Service().tools
        assert "error" in tools.run_sql("DROP TABLE sales_clean")
        assert "error" in tools.run_sql("ATTACH DATABASE '/tmp/x.db' AS x")
        assert tools.conn.execute("SELECT COUNT(*) FROM sales_clean").fetchone()[0] == 18290

    def test_run_sql_allows_select(self):
        from kbqa.service import Service

        result = Service().tools.run_sql("SELECT COUNT(*) AS n FROM sales_clean")
        assert result["rows"][0]["n"] == 18290
