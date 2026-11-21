from __future__ import annotations

from sqlalchemy import select

from shared.db.models import SearchLog
from tests.utils import keyword_search, upload_ok


def test_chinese_content_and_filename_modes(client, user):
    upload_ok(
        client,
        user,
        "人工智能导论.txt",
        "机器学习是人工智能的重要分支，涉及特征工程、模型评估与数据清洗。",
    )

    content = keyword_search(client, user, "机器学习", mode="content")
    assert content.status_code == 200, content.text
    payload = content.json()
    assert payload["total"] == 1
    assert payload["results"][0]["name"] == "人工智能导论.txt"
    assert "<mark>" in payload["results"][0]["highlight"]
    assert "机器" in payload["results"][0]["snippet"]

    filename = keyword_search(client, user, "人工智能导论", mode="filename")
    assert filename.json()["total"] == 1

    # Content-only term must not match filename mode and vice versa.
    assert keyword_search(client, user, "特征工程", mode="filename").json()["total"] == 0
    assert keyword_search(client, user, "红烧肉", mode="content").json()["total"] == 0

    all_mode = keyword_search(client, user, "特征工程", mode="all")
    assert all_mode.json()["total"] == 1


def test_phrase_search(client, user):
    upload_ok(
        client, user, "神经网络.txt", "深度神经网络使用反向传播算法来更新参数。"
    )
    phrase = keyword_search(client, user, '"反向传播"', mode="content")
    assert phrase.status_code == 200
    assert phrase.json()["total"] == 1


def test_pagination(client, user):
    for index in range(5):
        upload_ok(
            client,
            user,
            f"分页-{index}.txt",
            f"分页测试语料第{index}篇，用于验证搜索结果分页。",
        )

    first = keyword_search(client, user, "分页测试", mode="content", page=1, page_size=2)
    assert first.json()["total"] == 5
    assert len(first.json()["results"]) == 2

    third = keyword_search(client, user, "分页测试", mode="content", page=3, page_size=2)
    assert len(third.json()["results"]) == 1

    beyond = keyword_search(client, user, "分页测试", mode="content", page=9, page_size=2)
    assert beyond.status_code == 200
    assert beyond.json()["results"] == []


def test_html_and_markdown_extraction(client, user):
    html_text = (
        "<html><head><title>网页</title>"
        "<script>脚本独有词不应被索引</script>"
        "<style>.hidden{color:red}</style></head>"
        "<body><h1>网页标题</h1><p>网页正文独有词甲，包含<strong>强调</strong>内容。</p></body></html>"
    )
    upload_ok(client, user, "page.html", html_text, mime_type="text/html; charset=utf-8")
    markdown_text = (
        "# 笔记标题\n\n"
        "[链接文字](https://example.com) 与 ![图片说明](a.png)\n\n"
        "Markdown 正文独有词乙，用于验证正文提取。\n"
    )
    upload_ok(
        client, user, "note.md", markdown_text, mime_type="text/markdown; charset=utf-8"
    )

    assert keyword_search(client, user, "网页正文独有词甲", mode="content").json()["total"] == 1
    assert keyword_search(client, user, "脚本独有词", mode="content").json()["total"] == 0
    assert keyword_search(client, user, "正文独有词乙", mode="content").json()["total"] == 1
    # Link text is kept, URL markup is not part of the indexed body.
    assert keyword_search(client, user, "链接文字", mode="content").json()["total"] == 1


def test_deleted_file_disappears_from_search(client, user):
    created = upload_ok(client, user, "删除搜索.txt", "删除搜索测试唯一短语ABCXYZ")
    file_id = created["node"]["id"]
    assert keyword_search(client, user, "唯一短语ABCXYZ", mode="content").json()["total"] == 1

    assert client.delete(f"/api/nodes/{file_id}", headers=user.headers).status_code == 200
    assert keyword_search(client, user, "唯一短语ABCXYZ", mode="content").json()["total"] == 0


def test_search_results_are_owner_scoped(client, user):
    from tests.utils import register_user

    other = register_user(client, "search-other")
    upload_ok(client, user, "owner-only.txt", "只有所有者能搜索到的隔离语料QQQ")
    assert keyword_search(client, user, "隔离语料QQQ", mode="content").json()["total"] == 1
    assert keyword_search(client, other, "隔离语料QQQ", mode="content").json()["total"] == 0


def test_search_logs_written(client, user, db_session):
    upload_ok(client, user, "日志.txt", "搜索日志测试语料ZZZ")
    response = keyword_search(client, user, "搜索日志测试语料ZZZ", mode="content")
    assert response.status_code == 200

    db_session.expire_all()
    logs = db_session.scalars(
        select(SearchLog).where(SearchLog.user_id == user.user_id)
    ).all()
    assert logs
    assert any(log.search_mode == "content" for log in logs)
    assert all(log.elapsed_ms >= 0 for log in logs)
