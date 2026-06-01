# RAG Chunking Benchmark v2

这个 benchmark 用于测试不同来源材料在不同切片策略下的效果。

## 数据来源类型

- `github_md/`: GitHub Markdown 类材料。建议从 `https://github.com/liuup/claude-code-analysis` 拉取真实 `analysis/*.md`。
- `xiaohongshu_interview/`: 小红书面经类材料。本版更贴近真实抓取场景：关键词和时间筛选后，主体多数是求职/实习/面经相关，但噪声主要来自：
  - 非计算机岗位面经；
  - 个人感受、timeline、offer 玄学；
  - 过短帖子；
  - 评论区补充；
  - 图片 OCR 中混入的个人感受或低相关信息。
- `xiaolin_blog/`: 小林 Coding 风格技术长文。包含技术解释、标题、图解、代码块，也混入广告、公众号引流、推荐阅读等。

## 建议实验

比较以下切片策略：

1. 固定长度切片；
2. 结构切片 + 父子切片；
3. 语义切片；
4. LlamaIndex 或类似框架切片。

## 运行建议

先让 Claude Code 做 planning：

```bash
# 检查目录
find benchmark -maxdepth 4 -type f | sort

# 如果要拉 GitHub Markdown
python benchmark/scripts/fetch_github_materials.py
```

然后再实现切片与评测。
