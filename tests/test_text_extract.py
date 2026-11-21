from __future__ import annotations

from services.vfs_storage.text_extract import extract_text


def test_html_strips_scripts_and_styles(tmp_path):
    path = tmp_path / "page.html"
    path.write_text(
        "<html><head><script>alert('SECRETSCRIPT')</script>"
        "<style>.x{color:red}</style></head>"
        "<body><h1>标题文字</h1><p>正文段落内容</p><img alt='图片替代文字' src='a.png'></body></html>",
        encoding="utf-8",
    )
    result = extract_text(path, filename="page.html", mime_type="text/html")
    assert result.method == "html"
    assert "正文段落内容" in result.text
    assert "标题文字" in result.text
    assert "图片替代文字" in result.text
    assert "SECRETSCRIPT" not in result.text
    assert "color:red" not in result.text


def test_markdown_links_and_binary(tmp_path):
    path = tmp_path / "note.md"
    path.write_text(
        "# 标题\n\n[e链接文字](https://example.com) 和 ![图片描述](a.png)\n\n正文内容。\n",
        encoding="utf-8",
    )
    result = extract_text(path, filename="note.md")
    assert result.method == "markdown"
    assert "链接文字" in result.text
    assert "图片描述" in result.text

    binary = tmp_path / "blob.bin"
    binary.write_bytes(b"\x00\x01\x02\x03" * 100)
    binary_result = extract_text(binary, filename="blob.bin")
    assert binary_result.text == ""
    assert binary_result.method == "binary"


def test_gb18030_decoding(tmp_path):
    path = tmp_path / "gbk.txt"
    path.write_bytes("中文编码测试内容".encode("gb18030"))
    result = extract_text(path, filename="gbk.txt", mime_type="text/plain")
    assert result.method == "text"
    assert "中文编码测试内容" in result.text


def test_max_chars_truncation(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text("测试内容" * 500, encoding="utf-8")
    result = extract_text(path, filename="long.txt", max_chars=100)
    assert len(result.text) == 100
    assert result.truncated is True


def test_unknown_extension_falls_back_to_filename_only(tmp_path):
    path = tmp_path / "archive.xyz"
    path.write_bytes(b"binary data without text")
    result = extract_text(path, filename="archive.xyz")
    assert result.text == ""
    assert result.method == "binary"
