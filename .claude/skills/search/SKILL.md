---
name: search
description: Use when the user wants to find academic papers, search the local library, run keyword or semantic search, search by author, explore topics, or federate across library, explore databases, and arXiv.
---
# 文献搜索

在本地论文库中搜索文献。默认使用融合检索（关键词 + 语义向量合并排序），也支持单独使用某一种模式。

## 执行逻辑

1. 解析用户输入，判断搜索模式：
   - 如果用户明确要求"语义搜索"、"向量搜索"或"vsearch"，使用 `vsearch`
   - 如果用户明确要求"关键词搜索"、"全文搜索"或"FTS"，使用 `search`
   - 如果用户明确要找“证据片段”、“原文片段”、“行号定位”、“在哪一节/哪几行提到”，使用 `search --chunk`；若 chunk 索引尚未建立，先运行 `index --chunks`
   - 如果用户明确按作者搜索（如"找某某的论文"、"某某发表的"），使用 `search-author`
   - 如果用户要求按引用量排序（如"引用最高的"、"最经典的"、"top cited"），转交 `/citations` skill
   - **默认使用 `usearch`（融合检索）**——同时执行 FTS5 关键词搜索和 FAISS 语义搜索，合并去重排序。两路都命中的论文排名靠前。向量索引不可用时自动降级为纯关键词。
   - 如果用户要求跨库搜索（如"也搜一下 arXiv"、"在 explore 库里也找找"、"也搜 proceedings"、"全部来源"、"联邦搜索"），使用 `fsearch`

2. 从用户输入中提取：
   - **查询词**：用户想搜索的内容
   - **返回数量**：使用规范参数 `--limit N`；未指定则使用默认值
   - **年份过滤**：`--year 2023`（单年）、`--year 2020-2024`（范围）、`--year 2020-`（起始年至今）
   - **期刊过滤**：`--journal "Fluid Mechanics"`（模糊匹配）
   - **类型过滤**：`--type review`（模糊匹配，常见值：`review`、`journal-article`、`book-chapter`）

   **查询词拆分原则**：不要把“作者 + 年份 + 关键词/题名词”全部拼进同一个 query。这条规则同时适用于 `search`、`vsearch` 和 `usearch`：
   - `search` 会把整串文本交给 FTS5 `MATCH`；作者缩写、全名、标点或年份 token 只要和索引不一致，就可能让原本可命中的论文搜不出来。
   - `vsearch` 通常不会因此空结果，但作者/年份/期刊等限定词会作为噪声进入 query embedding，可能拉低相关论文分数或引入相近但不精确的结果。
   - `usearch` 同时跑 FTS 和向量；脏 query 可能让 FTS leg 失效，只剩语义命中，结果不再获得 `both` 加分。
   - 年份必须优先放到 `--year`，不要放进 query。
   - 明确按作者找时用 `search-author "<作者姓或姓名>"`，不要把作者混进主题 query。
   - 已知题名或主题时，query 保持为最稳定的题名/主题关键词；需要作者/年份约束时分步过滤或二次确认。
   - 如果第一次无结果，先去掉作者缩写、年份、机构、期刊等限定词，只保留题名核心词或主题词再搜。

3. 执行搜索命令：

**融合检索（默认）：**
```bash
scholaraio usearch "<查询词>" --limit <N> [--year <Y>] [--journal <J>] [--type <T>]
```

**关键词搜索：**
```bash
scholaraio search "<查询词>" --limit <N> [--year <Y>] [--journal <J>] [--type <T>]
```

**证据片段搜索（返回 paper、section、line range、snippet）：**
```bash
scholaraio search --chunk "<查询词>" --limit <N> [--year <Y>] [--journal <J>] [--type <T>]
```

**语义搜索：**
```bash
scholaraio vsearch "<查询词>" --limit <N> [--year <Y>] [--journal <J>] [--type <T>]
```

**作者搜索：**
```bash
scholaraio search-author "<作者名>" --limit <N> [--year <Y>] [--journal <J>] [--type <T>]
```

> **引用量排序**：使用 `/citations` skill 中的 `scholaraio top-cited` 命令。

**联邦搜索（跨库 + arXiv）：**
```bash
# 同时搜主库和 arXiv
scholaraio fsearch "<查询词>" --scope main,arxiv --limit <N>

# 同时搜主库和 proceedings
scholaraio fsearch "<查询词>" --scope main,proceedings

# 同时搜主库和所有 explore 库
scholaraio fsearch "<查询词>" --scope main,explore:*

# 搜指定 explore 库
scholaraio fsearch "<查询词>" --scope explore:my-survey

# 仅搜 arXiv（在线查询，不需要本地数据）
scholaraio fsearch "<查询词>" --scope arxiv

# 全部来源
scholaraio fsearch "<查询词>" --scope main,proceedings,explore:*,arxiv
```

`--scope` 支持逗号分隔组合：`main`（主库融合搜索）、`proceedings`（论文集子论文）、`explore:<名称>` 或 `explore:*`（explore 库）、`arxiv`（在线 arXiv API）。默认 scope 为 `main`。arXiv 结果会标注 `[已入库]` 表示该论文已在本地库中。

4. 将搜索结果整理后呈现给用户。融合检索结果中每项标注了匹配来源：
   - `both`：关键词和语义都命中（最相关）
   - `fts`：仅关键词命中
   - `vec`：仅语义命中

5. **复杂查询**：当 CLI 参数组合无法满足需求时（如按一作姓氏首字母筛选、多条件交叉、自定义排序等），直接写 Python 读 configured papers library 下的 `*/meta.json` 做查询。JSON 关键字段：

```
title, authors, first_author, first_author_lastname, year, doi, journal,
abstract, paper_type, citation_count (dict: crossref/semantic_scholar/openalex),
ids, toc, l3_conclusion
```

## 示例

用户说："帮我搜一下 turbulent boundary layer 相关的论文"
→ 执行 `usearch "turbulent boundary layer"`

用户说："用语义搜索找 drag reduction 的文献，给我前5篇"
→ 执行 `vsearch "drag reduction" --limit 5`

用户说："找 Liao Z-M 的论文"
→ 执行 `search-author "Liao"`

用户说："我库里引用最高的论文有哪些"
→ 转交 `/citations` skill（使用 `top-cited` 命令）

用户说："2020年以后关于 drag reduction 的论文"
→ 执行 `usearch "drag reduction" --year 2020-`

用户说："找 subgrid scale model 的原文证据片段，最好有行号"
→ 若未建 chunk 索引先执行 `index --chunks`，再执行 `search --chunk "subgrid scale model"`

用户说："找 Moin 1982 numerical investigation turbulent channel flow"
→ 执行 `usearch "numerical investigation turbulent channel flow" --year 1982`；必要时再用 `search-author "Moin" --year 1982` 交叉确认，不要执行 `search "P Moin 1982 numerical investigation turbulent channel flow"`

用户说："JFM 上发的湍流论文"
→ 执行 `usearch "turbulence" --journal "Fluid Mechanics"`

用户说："库里引用最高的 review 文章"
→ 转交 `/citations` skill（使用 `top-cited --type review` 命令）

用户说："帮我在 arXiv 上也搜一下 physics-informed neural network"
→ 执行 `fsearch "physics-informed neural network" --scope main,arxiv`

用户说："所有来源都搜一下 drag reduction，包括 explore 库"
→ 执行 `fsearch "drag reduction" --scope main,proceedings,explore:*,arxiv`

用户说："在我之前建的 wall-bounded-turbulence explore 库里搜 channel flow"
→ 执行 `fsearch "channel flow" --scope explore:wall-bounded-turbulence`

用户说："连 proceedings 一起搜 granular damping"
→ 执行 `fsearch "granular damping" --scope main,proceedings`
