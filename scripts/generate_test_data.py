#!/usr/bin/env python
"""Generate realistic Chinese test documents and upload them via the real API.

Usage (services already running):

    python scripts/generate_test_data.py --register --count 10

The script logs in (or registers), creates a "示例数据" folder, and uploads
TXT/HTML/Markdown files.  It also uploads one duplicate-content file to
demonstrate MD5 deduplication.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable

import httpx

TOPICS = [
    {
        "name": "机器学习与人工智能导论",
        "tag": "人工智能 机器学习 监督学习 特征工程",
        "paragraphs": [
            "机器学习是人工智能的重要分支，研究如何让计算机从数据中自动学习规律，并用学到的模型完成预测或决策。",
            "监督学习使用带标签的数据训练模型，常见任务包括分类和回归。无监督学习则从没有标签的数据中发现聚类结构和潜在模式。",
            "特征工程决定了模型能观察到什么信息。高质量的特征、合理的训练集划分和严格的评估流程，往往比盲目增加模型复杂度更有效。",
            "模型评估需要区分训练集、验证集和测试集，避免过拟合。准确率、精确率、召回率、F1 值和 AUC 都是常用的评价指标。",
        ],
    },
    {
        "name": "深度学习与神经网络笔记",
        "tag": "深度学习 神经网络 反向传播 卷积网络",
        "paragraphs": [
            "深度学习通过多层神经网络自动学习数据的层次化表示。卷积神经网络擅长处理图像，循环神经网络和 Transformer 擅长处理序列数据。",
            "反向传播算法利用链式法则计算梯度，优化器根据梯度更新参数。学习率、批大小、正则化和数据增强都会显著影响训练效果。",
            "残差连接缓解了深层网络的梯度消失问题，注意力机制让模型能够动态关注输入中的重要部分。",
            "训练深度模型需要关注过拟合、训练稳定性和计算资源。迁移学习可以利用预训练模型减少标注数据需求。",
        ],
    },
    {
        "name": "数据库系统与索引原理",
        "tag": "数据库 MySQL 索引 B+树 事务",
        "paragraphs": [
            "关系数据库使用表来组织数据，通过主键、外键、唯一约束和检查约束保证数据完整性。MySQL 的 InnoDB 引擎支持事务和行级锁。",
            "B+ 树索引把数据按键值有序组织，可以高效支持等值查询和范围查询。联合索引遵循最左前缀原则，设计索引时要结合真实查询模式。",
            "事务具有原子性、一致性、隔离性和持久性。不同隔离级别在并发性能与一致性之间做出权衡。",
            "慢查询优化通常从执行计划开始，关注是否走索引、扫描行数、回表次数以及排序和临时表开销。",
        ],
    },
    {
        "name": "搜索引擎倒排索引设计",
        "tag": "搜索引擎 倒排索引 分词 BM25 相关度",
        "paragraphs": [
            "搜索引擎的核心是倒排索引：从词项映射到包含该词项的文档列表，并记录词频、位置等统计信息。",
            "中文检索需要先进行分词。jieba 可以把连续的中文文本切分为词，并支持搜索模式切分以提高召回率。",
            "BM25 是常用的相关度排序算法，综合考虑词频、逆文档频率和文档长度。标题字段通常赋予更高权重。",
            "搜索摘要从原文中截取命中词附近的上下文，并对关键词高亮。分页和候选集过滤可以控制响应时间。",
        ],
    },
    {
        "name": "云存储与MD5内容寻址",
        "tag": "云存储 MD5 去重 内容寻址 引用计数",
        "paragraphs": [
            "内容寻址存储使用文件内容的哈希值作为物理文件名。相同内容只保存一份，不同逻辑文件可以引用同一个物理对象。",
            "MD5 适合普通文件去重，但不应作为高安全场景的唯一完整性保证。系统还应记录文件大小，并在复用对象时校验大小。",
            "引用计数用于判断物理内容是否仍被逻辑文件使用。删除逻辑文件时减少计数，计数归零后再清理磁盘文件。",
            "两级分片目录可以避免单个目录文件过多，例如使用 MD5 的前两位和接下来两位作为子目录。",
        ],
    },
    {
        "name": "红烧肉家常做法",
        "tag": "菜谱 红烧肉 烹饪 家常菜",
        "paragraphs": [
            "准备五花肉、冰糖、生姜、八角、桂皮、生抽和老抽。五花肉切成大小均匀的方块，冷水下锅焯水后捞出沥干。",
            "锅中放少量油，加入冰糖小火炒出琥珀色糖色，放入五花肉翻炒上色，再加入姜片和香料炒香。",
            "加入生抽、老抽和足量热水，大火烧开后转小火炖煮约一小时，最后开大火收汁，撒上葱花即可。",
            "关键是控制火候：炒糖色不要炒糊，炖煮时保持微沸，收汁时要不断翻动避免粘锅。",
        ],
    },
    {
        "name": "杭州西湖旅行日记",
        "tag": "旅行 杭州 西湖 风景 游记",
        "paragraphs": [
            "清晨沿着苏堤散步，湖面薄雾还未散去，远处的雷峰塔在树影之间若隐若现。",
            "断桥边游客不多，微风吹过荷叶，空气里有淡淡的水汽。午后去了灵隐寺，山间石阶两侧是高大的古树。",
            "傍晚在湖边找了一家小馆，点了西湖醋鱼和龙井虾仁。夜色降临后，音乐喷泉在湖面上亮起。",
            "建议避开节假日高峰，清晨和傍晚的西湖最安静，也最适合拍照。",
        ],
    },
    {
        "name": "敏捷项目管理实践",
        "tag": "项目管理 敏捷 迭代 Scrum 需求",
        "paragraphs": [
            "敏捷开发强调小步快跑和持续反馈。团队以固定长度的迭代交付可工作的软件，并在评审中收集用户反馈。",
            "需求以用户故事描述，明确角色、目标和价值。迭代计划会从待办列表中挑选优先级最高且工作量合理的条目。",
            "每日站会用于同步进展和暴露阻塞，而不是汇报工作。回顾会议总结经验并形成下一轮的改进项。",
            "敏捷不是没有计划，而是通过短周期计划和持续调整降低不确定性。透明的看板有助于发现流程瓶颈。",
        ],
    },
]


def html_document(title: str, paragraphs: Iterable[str]) -> str:
    body = "".join(f"<p>{paragraph}</p>" for paragraph in paragraphs)
    return (
        f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title>"
        f"<style>body{{font-family:sans-serif}}</style>"
        f"<script>console.log('should not be indexed');</script></head>"
        f"<body><h1>{title}</h1>{body}<footer>CloudDisk test data</footer></body></html>"
    )


def markdown_document(title: str, tag: str, paragraphs: Iterable[str]) -> str:
    lines = [f"# {title}", "", f"> 标签：{tag}", ""]
    for index, paragraph in enumerate(paragraphs, 1):
        lines.append(f"## 第 {index} 节")
        lines.append("")
        lines.append(paragraph)
        lines.append("")
    lines.append("相关资料：[CloudDisk](http://localhost:8000)")
    lines.append("")
    return "\n".join(lines)


def build_files(count: int) -> list[tuple[str, str, str]]:
    """Return (filename, mime, text) triples."""
    files: list[tuple[str, str, str]] = []
    extensions = [
        ("txt", "text/plain; charset=utf-8"),
        ("md", "text/markdown; charset=utf-8"),
        ("html", "text/html; charset=utf-8"),
    ]
    for index in range(count):
        topic = TOPICS[index % len(TOPICS)]
        cycle = index // len(TOPICS)
        title = topic["name"] + (f"（{cycle + 1}）" if cycle else "")
        ext, mime = extensions[index % len(extensions)]
        if ext == "html":
            text = html_document(title, topic["paragraphs"])
        elif ext == "md":
            text = markdown_document(title, topic["tag"], topic["paragraphs"])
        else:
            text = title + "\n\n" + "\n\n".join(topic["paragraphs"])
        files.append((f"{title}.{ext}", mime, text))

    if files:
        # Duplicate content with a different filename (MD5 dedup demo).
        first_name, first_mime, first_text = files[0]
        duplicate_name = f"副本-{first_name}"
        files.append((duplicate_name, first_mime, first_text))
    return files


def login(client: httpx.Client, base_url: str, username: str, password: str, register: bool) -> str:
    response = client.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
    )
    if response.status_code != 200:
        if not register:
            raise SystemExit(
                f"登录失败: {response.status_code} {response.text}\n"
                "首次使用请加 --register，或先运行 python sql/seed.py"
            )
        response = client.post(
            f"{base_url}/api/auth/register",
            json={"username": username, "password": password},
        )
        if response.status_code not in (200, 201):
            raise SystemExit(f"注册失败: {response.status_code} {response.text}")
    return response.json()["access_token"]


def ensure_folder(client, base_url, headers, root_id: int, name: str) -> int:
    listing = client.get(
        f"{base_url}/api/nodes",
        params={"parent_id": root_id, "page_size": 200},
        headers=headers,
    )
    listing.raise_for_status()
    for node in listing.json()["items"]:
        if node["node_type"] == "FOLDER" and node["name"] == name:
            return int(node["id"])
    response = client.post(
        f"{base_url}/api/folders",
        json={"name": name, "parent_id": root_id},
        headers=headers,
    )
    if response.status_code not in (200, 201):
        raise SystemExit(f"创建文件夹失败: {response.status_code} {response.text}")
    return int(response.json()["id"])


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload CloudDisk demo documents")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="demo")
    parser.add_argument("--password", default="demo123456")
    parser.add_argument("--register", action="store_true", help="register if login fails")
    parser.add_argument("--count", type=int, default=8, help="number of topic documents")
    parser.add_argument("--folder", default="示例数据")
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    with httpx.Client(timeout=120.0) as client:
        token = login(client, base_url, args.username, args.password, args.register)
        headers = {"Authorization": f"Bearer {token}"}

        me = client.get(f"{base_url}/api/auth/me", headers=headers)
        me.raise_for_status()
        root = client.get(f"{base_url}/api/nodes/root", headers=headers)
        root.raise_for_status()
        root_id = int(root.json()["id"])
        folder_id = ensure_folder(client, base_url, headers, root_id, args.folder)

        uploaded = 0
        deduplicated = 0
        failed = 0
        skipped = 0
        for filename, mime, text in build_files(max(1, args.count)):
            response = client.post(
                f"{base_url}/api/files/upload",
                headers=headers,
                data={"parent_id": str(folder_id), "description": "由 generate_test_data.py 生成"},
                files={"file": (filename, text.encode("utf-8"), mime)},
            )
            if response.status_code == 409:
                skipped += 1
                print(f"[skip] {filename}: 同名文件已存在（重复运行脚本时会跳过）")
                continue
            if response.status_code not in (200, 201):
                failed += 1
                print(f"[fail] {filename}: HTTP {response.status_code} {response.text[:200]}")
                continue
            payload = response.json()
            uploaded += 1
            if payload.get("deduplicated"):
                deduplicated += 1
            index = payload.get("index") or {}
            print(
                f"[ok] {filename}  node={payload['node']['id']}  "
                f"md5={payload['node']['content_md5']}  "
                f"keyword={index.get('keyword_status')} semantic={index.get('semantic_status')} "
                f"{'(复用已有物理内容)' if payload.get('deduplicated') else ''}"
            )

        print(
            f"\n完成：上传 {uploaded} 个文件，其中 {deduplicated} 个复用了已有 MD5 内容，"
            f"跳过 {skipped} 个同名文件，失败 {failed} 个。"
        )
        print(f"文件夹 node_id={folder_id}；可在前端「{args.folder}」中查看。")
        print("示例：搜索“机器学习”、“数据库索引”，或切换到语义搜索输入“如何给文件去重”。")
        return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
