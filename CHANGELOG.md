# General News Extractor Changelog

## Unreleased

### Security

1. SSL 证书校验失败不再静默降级：此前 `fetch_html` 捕获 `SSLCertVerificationError` 后直接用不校验的 `SSLContext` 重试，且没有任何日志，等于对所有站点默认关闭证书校验。现在默认 `LOG.error` 并抛出原异常；只有显式设置 `NEWS_INSECURE_SSL=1`（接受 `true`/`yes`/`on`）才降级重试，并留下 WARNING

### Bug fix

1. 新增 `raw_content` 列（`SCHEMA_VERSION` 升到 4，老库启动时自动补列）：`content` 仍是 `clean_text()` 归一化后的单行文本，GNE 原始正文的段落换行保存在 `raw_content`，正文展示与导出不再丢段落。该字段只在 `GET /v1/articles/{id}` 详情返回（列表接口即使 `include_content=true` 也只给单行 `content`）。历史行只补列不自动回填（原文已不可得），需要换行正文时用 `--force` 重抓，或用新增的 `backfill_raw_content.py` 批量重抓回填（只写 `raw_content`，不改 `content`）
2. `extract_article` 不再向 stdout 打印进度：15 处 `print` 改为标准库日志（`url_extract` logger），作为库被 API 调用时不再污染宿主进程输出；命令行入口保留 `print` 并配置了 `logging.basicConfig`
3. `ArticleOut` 补上 `raw_content` 字段：此前 db 层已把换行正文塞进响应字典，但响应模型没有该字段，Pydantic 默认 `extra=ignore` 把它静默丢弃，导致详情接口拿不到换行正文
4. 抓取的 `ok` 分支补写 `raw_content`：此前只有 `unknown_date` 分支传了该字段，绝大多数正常入库的文章 `raw_content` 恒为 NULL
5. `backfill_raw_content.py` 的失败语义拆分：抓取失败与「UPDATE 未命中」（并发回填/行已删除）分别计入失败与跳过；失败清单默认写到与 `--db` 同目录的 `backfill_failed.txt`（容器内即 `/app/data/backfill_failed.txt`，随 `./data` 挂载保留，不再随 `--rm` 容器丢失），每次运行覆盖写但保留本次 `--skip-file` 中的条目（标注「上次失败，本次跳过」），避免死链被洗掉后重新参与抓取；读取兼容 UTF-8 BOM，各列压成单行以免破坏 TSV，`--failed-list` 禁止指向数据库文件或目录；`--skip-file` 可在下次运行中排除这些死链，避免反复占用 `--limit` 名额；新增 `regression/test_backfill.py`（5 个离线用例）覆盖候选 skip/limit、清单列清洗与 BOM、skip 条目保留、输出路径防呆

### CI

1. PR 不再运行真实网络抓取：`live-news` job 只在 push `master`、夜间定时与手动触发时执行，避免站点反爬/改版把无关 PR 卡住
2. 新增夜间定时（北京时间 02:00）真实抓取，失败自动开/更新 issue 留痕
3. `python-checks` 矩阵补到 Python 3.14（与 classifier 一致），并新增 `ruff check .`、`mypy` 与 `regression` 离线回归
4. 62 篇上游 HTML 样本的抽取基线随仓库提交（`regression/baseline.json`），PR 用离线回归替代真实网络验证
5. 固定 `ruff==0.17.3` 与 `mypy==2.4.1`：规则集较宽，跟随上游最新版会让无关 PR 因新增规则变红；基线比对同时排除 `gne_version`，发版 bump 版本号不再要求重建基线
6. `concurrency` 按「工作流 + 分支 + 事件类型」分组，夜间定时任务不再被 push 取消（`cancel-in-progress` 仅对非 schedule 为 true），避免站点改版的夜间信号被白天的提交吞掉
7. `docker-build-full`（含浏览器镜像 + 镜像内实时抓取）只在夜间与手动触发时运行：push 时 `live-news` 已在宿主机跑同一组 10/10/10，不再重复消耗约 45 分钟

### Refactor

1. `pyproject.toml` 补齐 `[tool.ruff]` 与 `[tool.mypy]` 配置，规则集从 4 条扩到 `E,F,W,I,UP,B`，CI 与本地共用同一份配置
2. `.dockerignore` 排除 `tests/` 与 `regression/`，镜像不再打包 62 个样本
3. 离线回归基线补 `meta_keys`/`meta_sha1` 与 `is_video` 字段：此前只比对 6 个字段，meta 抽取或视频页判定变化时回归仍是绿的
4. 新增 `regression/test_list_page.py`（列表页解析，3 个用例）与 `regression/test_video_detection.py`（视频页判定，5 个用例）：`tests/` 样本都是文章详情页，这两块此前完全没有回归覆盖
5. `[tool.mypy] files` 纳入 `regression/`，新增的回归套件与主代码同一套类型检查
6. `gne/extractors/list.py` 的 `print("找不到 feature！")` 改为 `logging.warning`：库函数不再向 stdout 输出
7. `live_news_pool.json` 的视频池从 5 个来源站点扩到 10 个（新增网易、正北方网、安徽网、中新网、西部网，各 1 条经 `check_video` 验证的页面；同时从新华社 26 条中删掉 5 条，保持 50 条总量），视频类抽样的来源集中度下降

### Docs

1. `pypi_desc.md` 澄清 PyPI 上的 `gne` 属于上游 GeneralNewsExtractor，本项目不向 PyPI 发布；`pyproject.toml` 同步标注 source-distribution only 与真实维护者
2. `AGENTS.md` 同步 SSL 开关、`raw_content`、CI 分层与离线回归基线
3. `README.md` 增加包名冲突警告：本项目发行包名同为 `gne`，与 PyPI 上游同名，`pip install -e .` 会生成 `gne-1.1.2.dist-info`，请使用独立虚拟环境，不要与 PyPI 的 `gne` 混装

## 1.1.2 (2026-11-03)

### Refactor

1. GNE 抽取核心重构：`gne/` 拆分为 `engine.py`（主流程）、`config.py`、`constants.py`、`exceptions.py`、`html.py`、`text.py` 与 `extractors/{content,title,time,author,meta,list}.py`；旧 `gne/extractor/` 路径保留兼容层。`extract()` 参数顺序与默认值、返回字段、正文密度算法、字段回退链均未变（62 篇样本 A/B：title / content / publish_time / author / images / status 六项 0 diff）
2. 清理上游演示资产：`docs/`、`screenshots/`、`site/`、`test_list_page/`、`visiable_test/`、`example*.py` 等 84 个文件（-30144 行）移除，仓库只保留抽取库与抓取/API 代码

### Docs

1. 许可证元数据统一为 GPL-3.0-only：`LICENSE` 自 89b1de8 起就是 GPLv3，但 `pyproject.toml` 与 `AGENTS.md` 一直写 MIT；同步 classifier、`uv.lock`、README 新增的许可证章节（GPLv3 义务仅在向他人传递副本时触发，源代码按第 6 节规定的方式提供，本地运行或仅提供服务不触发）
2. `pypi_desc.md` 说明 PyPI 上的 `gne` 只包含抽取库，完整抓取/API 需从源码或 Docker 部署；补齐 `git clone`、`playwright install chromium` 与可选依赖的安装步骤
3. `AGENTS.md` 新增「回归基线（不在仓库内）」小节：给出用 `a45b464` 与临时目录复现 A/B 回归的步骤

### Chore

1. 正文标点集合 `PUNCTUATION` 冻结为 `frozenset`（只读使用，避免运行时被意外修改）

## 1.1.1 (2026-11-02)

### Bug fix

1. `delay` 现在同时作用于文章详情请求：此前只在列表页之间等待，单站点连续抓文章时实测间隔仅 0.13s（配置 0.5s 未生效），批量抓取容易触发反爬
2. `old` 状态不再永久跳过：改为按入库时记录的发布时间复核，只要它仍早于当前窗口才跳过；放宽 `--days`/`--since` 后会自动重抓，历史数据不必再靠 `--force` 全量重抓
3. `--limit-per-source` 只统计实际处理的条目，已入库被跳过的条目不占名额；此前第二次运行会被同一批 skip 卡死无法前进
4. 入库 `images` 补全为绝对 URL：GNE 抽出的 `//cdn.example.com/x.jpg` 这类协议相对地址会按文章 URL 解析，下游可直接使用
5. 只有 401/403/406/429 才回退浏览器：404/410/500 等错误渲染后仍是同样错误，此前每条失效链接都要白等一次浏览器启动（实测单条 1.84s）
6. `fetch_html` 支持 gzip 响应：请求带 `Accept-Encoding: gzip` 并按 `Content-Encoding` 逆序解压（支持叠加编码与裸 deflate 流），实测三个真实页面的传输量分别降到 25%/66%/33%；无法解码的编码抛 `UnsupportedContentEncoding`，不再静默把压缩字节当 HTML 解码成乱码正文
7. `fetch_html` 复用模块级 `SSLContext`：此前每个请求都新建上下文（实测 19.8ms/次），证书异常站点仍按需惰性创建不校验上下文
8. `/v1` 读取侧同样归一化 `images`：v1.1.1 之前入库的历史行可能仍是协议相对地址，接口输出统一为绝对 URL
9. `normalize_images` 只保留 `http/https`：`data:`、`javascript:`、`ftp:` 等 scheme 会从图片列表中过滤掉
10. `_decompress` 增加解压输出上限 `MAX_DECODED_BYTES`（32MB），压缩炸弹会被拒绝而不是先吃满内存

### Performance

1. 新增两条查询覆盖索引（`SCHEMA_VERSION` 升到 3，老库启动时自动补建）：`(status, publish_time DESC, source, url)` 让 `GET /v1/articles` 从 10 万行的 220ms 降到 0.1ms；`(source, publish_time DESC, updated_at DESC, timezone, status)` 让 `GET /v1/sources` 从 3.8s 降到 56ms
2. Playwright 浏览器按线程复用，单页渲染从 4.9s 降到约 1.2s（3 页均摊）；每页使用独立 `BrowserContext` 隔离 cookie，`run_source` / `cmd_test` / CLI 入口结束时调用 `close_thread_browser()` 释放；浏览器崩溃时先回收旧 driver 再重启，launch 失败也不会留下孤儿进程
3. 渲染等待不再固定 `wait_for_timeout(2500)`，改为等 `networkidle` 且最多再等 1.5s：文档已就绪时立即返回，实测单页从 3.06s 降到 0.56s；单页导航失败不再销毁整只可复用的浏览器

### Test

1. `smoke_test.py` 增加图片地址补全、浏览器回退状态码白名单、两条性能索引是否建齐、`old` 在窗口放宽后是否重抓、gzip/deflate/叠加编码/未知编码/截断流、以及 v2→v3 老库升级补建索引的回归断言
2. `api_smoke.py` 增加“入库图片必须是绝对 URL”的断言（针对 extract 作业真正产出的那一行，并另有一条 `//cdn` 种子行覆盖读取侧归一化）

### Docs

1. `AGENTS.md` 同步 delay 语义、`--limit-per-source` 只计实际处理、`old` 重抓规则、覆盖索引与 `SCHEMA_VERSION = 3` 迁移说明、新增共享接口 `normalize_images` / `should_skip_stored` / `close_thread_browser` / `BROWSER_FALLBACK_HTTP_CODES`
2. `README.md` 补充 `--limit-per-source` 的计数语义

## 1.1.0 (2026-10-31)

### New Feature

1. 新增 Docker 部署：`Dockerfile`（`python:3.12-slim` 按 digest 固定 + `docker/constraints.txt` 锁定依赖）、`docker-compose.yml`、统一入口 `docker/entrypoint.sh`（`run`/`init`/`test`/`smoke`/`live-news`/`api`/`shell`）与一键脚本 `docker-deploy.sh`/`docker-deploy.bat`。默认镜像内置 Playwright Chromium，保证 JS 渲染回退可用
2. 新增标准新闻 HTTP API（`/v1`，FastAPI + Uvicorn）：`GET /v1/articles`、`GET /v1/articles/{article_id}`、`GET /v1/sources`、`GET /v1/stats`、`GET /v1/health`、`POST /v1/jobs`、`GET /v1/jobs`、`GET /v1/jobs/{id}`。列表响应统一为 `{"data": [...], "meta": {...}}`，错误统一为 `{"error": {"code", "status", "message", "details"?}}`
3. 新增稳定文章 ID：`article_id = sha256(f"{source}\x1f{url}").hexdigest()[:32]`，`articles` 增加该列并建唯一索引；旧库启动时按 `PRAGMA user_version` 一次性迁移（`SCHEMA_VERSION = 2`），回填按 2000 行分批
4. 新增 `crawl_jobs` 作业表与单进程作业队列：`POST /v1/jobs` 支持 `crawl`（按站点配置批量抓取）与 `extract`（单 URL 抽取入库）；相同类型与参数的未完成作业复用同一 ID；`NEWS_MAX_PENDING_JOBS`（默认 50）超限返回 429；已结束作业保留 30 天后自动清理
5. `articles` 新增 `timezone` 列记录写入时的站点时区，接口据此输出 `published_at`（源时区 ISO 8601）、`published_at_utc`（UTC）与 `published_raw`（数据库原值）
6. 新增可选鉴权：设置 `NEWS_API_KEY` 后校验 `X-API-Key` 或 `Authorization: Bearer`；`NEWS_REQUIRE_AUTH=1` 时未配置 key 一律拒绝
7. 新增 `[api]` 可选依赖与 `api` 启动入口（`docker compose up -d api`，默认绑定 `127.0.0.1:8000`，交互文档 `/docs`）

### Breaking Changes

1. 移除原生部署入口：删除 `deploy.sh`、`deploy.bat`、`deploy.ps1`，Docker 成为唯一受支持的部署方式
2. 容器内数据默认落在 `/app/data/news.db`（宿主机 `./data/news.db`），可用 `DATA_DIR` / `DB_PATH` 覆盖

### Security

1. `POST /v1/jobs` 的 `extract` 默认拒绝回环、内网与保留地址，HTTP 重定向逐跳复检；`NEWS_ALLOW_PRIVATE_URLS=1` 才放开
2. `extract` 作业默认禁用浏览器回退（`NEWS_EXTRACT_ALLOW_BROWSER=1` 可放开），避免页面 JS 绕开地址限制
3. API 默认只绑定 `127.0.0.1`；对局域网或公网暴露需显式设置 `NEWS_API_BIND` 并配置强随机 `NEWS_API_KEY`

### Bug fix

1. `save_article()` 改为部分更新语义：抓取失败只更新 `status`/`error`，不再清空已入库的正文与元数据
2. `PRAGMA journal_mode=WAL` 切换改为校验返回值 + 短重试，旧库并发初始化不再抛 `database is locked`
3. 列表页判 `old` 的分支补写 `timezone`；`/v1/sources` 的 `latest_published_at` 改用行内时区，避免改配置后时间偏移
4. 修正视频页 JSON-LD 误判与日期截断，视频页改用 meta/JSON-LD/URL 提取标题、简介与发布时间

### CI

1. 新增 `live-news` job（PR 与 push 均执行）：从 200 个真实页面池抽样 10 静态 / 10 JS 渲染 / 10 视频，不足即失败
2. 新增 `python-checks`（Python 3.10–3.13）、`shell-and-yaml`、`docker-build`、`docker-build-full`（含 Chromium 启动校验）四个 job
3. `docker-build-full` 的镜像内实时验证由 `continue-on-error` 改为硬门槛

### Docs

1. README 增加 Docker 部署、新闻 API（端点、参数、鉴权、错误信封、安全提示）与实时新闻抓取验证章节
2. AGENTS 同步 API 契约、数据库迁移、作业队列、CI 拓扑与实时新闻池维护方法
3. 新增 `live_news_pool.json` 页面池（100 静态 / 50 JS 渲染 / 50 视频）与抽样补抽机制说明

## 1.0.2 (2026-09-30)

### Bug fix

1. 修复正文碎片被当作成功结果入库的问题：`content_needs_render()` 原先只判断空正文与“加载中”类占位词，导致 `正在阅读：<标题>` 这类导航碎片（央视文章页约 47 字）以 `ok` 状态入库，而 `ok` 会被后续运行永久跳过。新增 `MIN_CONTENT_LENGTH = 100` 与 `content_is_too_short()`，正文不足 100 字即触发浏览器渲染回退
2. 渲染回退后正文仍不足 100 字时记 WARNING 并照常入库；渲染回退本身失败时维持 `no_content`（下次重试），避免一次环境故障就把碎片永久写成 `ok`
3. 单 URL 抓取流程 `url_extract.extract_article()` 同步同一规则

### Docs

1. 新增 AGENTS.md 多 Agent 协作开发说明，并在 README 项目结构表中引用
2. 同步 AGENTS.md §11.1 技术债与 §12.4 共享接口约定

## 0.4.1 (2026-03-02)

### Bug fix

1. 修复 USELESS_ATTR 子串匹配导致正文被误删的严重 bug：短关键词（如 `ad`、`nav`）会误匹配含有这些子串的正常 class/id（如 `padding`、`canvas`、`headline`），改为按 `-` `_` 空格拆分后的词级匹配
2. 添加 meta author 校验（`_is_valid_author`），过滤纯数字、邮箱、占位符等无效作者值
3. 添加日期校验（`_is_valid_date`），拒绝月份 >12 或日期 >31 的匹配结果，避免版本号等被误识别为日期

## 0.4.0 (2026-03-02)

### Performance

1. 移除 numpy 依赖，改用标准库 `math` 模块计算对数，减少安装体积、加快 import 速度
2. 预编译所有正则表达式（`DATETIME_PATTERN`、`AUTHOR_PATTERN`、BR 标签、高权重关键词），避免运行时重复编译
3. `GeneralNewsExtractor` 预创建各 extractor 实例，`extract()` 调用间复用，不再每次 new
4. 最长公共子串算法改为滚动数组，空间复杂度从 O(n*m) 降至 O(min(n,m))
5. `count_punctuation_num` 改为生成器表达式，更 Pythonic

### Bug fix

1. 修复正则表达式字符类 bug：`[-|/|.]` → `[-/.]`、`[：|:| |丨|/]` → `[：: 丨/]`，`|` 在 `[]` 中是字面量而非"或"
2. 修复 `normalize_node` 中匹配到 USELESS_ATTR 后 `break` 退出整个循环的 bug，改为收集后批量删除
3. 修复 `normalize_node` 遍历过程中修改树结构的问题，先将 `iter_node` 结果转为 list
4. 修复 `ArticleClassifier` 中 `classfy_by_weight` 拼写错误及缺失的 return 语句
5. 清理 `MetaExtractor` 中未实现的 `extract_meta` 方法

### Enhancements

1. 新增 `og:title` meta 标签提取作为标题候选
2. 新增 `<meta name="author">` 提取，优先于正则匹配
3. 新增 `date`、`DC.date` 等 meta 时间 XPath
4. USELESS_TAG 移除 `blockquote`（新闻正文常用引用），新增 `nav`、`aside`
5. USELESS_ATTR 新增 `sidebar`、`navigation`、`nav`、`breadcrumb`、`ad`、`advertisement`，并改为子串匹配同时检查 `id` 属性
6. HIGH_WEIGHT_ARRT_KEYWORD 新增 `post_body`、`entry-content`、`article-body`、`story-body`、`main-content`

### Infrastructure

1. 依赖管理从 pipenv 迁移到 uv，使用 `pyproject.toml` + `uv.lock`
2. 删除 `Pipfile`、`Pipfile.lock`、`setup.py`、`setup.cfg`、`requirements.txt`、`MANIFEST.in`
3. 升级 lxml 最低版本至 4.9.1（修复 CVE-2022-2309）
4. Python 版本支持范围更新为 3.8 - 3.13

## 0.3.1 (2024-04-17)

### Bug fix

有一些网站源代码不规范，在html中间突然出现</html>。末尾又出现一次</html>.这种情况下，会导致解析出错。现在已经修复。

## 0.3.0 (2021-10-07)

### New Feature

1. 基于可视化区域，更准确地识别正文

### Bug fix

* 修复下面这种情况时，无法正确寻找正文的 bug

```html
<div>
我是正文我是正文我是正文<a href="xxx">关键词1</a>我是正文我是正文我是正文我是正文
我是正文我是正文我是正文我是正文我是正文<a href="xxx">关键词2</a>我是正文我是正文
我是正文
</div>
```

* 统计一个标签下面的 p 标签的时候，应该把这个标签下面的直接文档数也统计进去

## 0.2.6 (2021-02-17)

### Bug fix

1. 修复 extract_by_htag_and_title 在发现 H 标签中的文本与 title 标签的文本在最小公共子串长度小于4时被认为是标题的问题。

## 0.2.5 (2020-12-21)

### Bug fix

1. 在使用 title 标签提取标题时，对于分隔符-_|分割的第一段内容，必须要大于4个字符才当做标题，否则会返回整个 title 标签的文本。

## 0.2.4 (2020-10-06)

### Features

1. 预处理时，把 <footer>标签移除。

## 0.2.3 (2020-09-15)

### Bug fix

1. `USELESS_ATTR`对应的节点，只有 class 完全匹配才需要删除。之前包含就删除的匹配方式会导致 ifeng 的正文被删除。

## 0.2.2 (2020-08-02)

### Features

1. 指定列表页特征，自动提取列表页数据


## 0.2.1 (2020-06-27)

### Feature

1. 不再需要计算文本密度的标准差
2. 🚀减少重复计算，大幅度提升分析速度

## 0.2.0 (2020-06-06)

### Feature

1. 优化标题提取逻辑，根据@止水 和 @asyncins 的建议，通过对比 //title/text()中的文本与 <h> 标签中的文本，提取出标题。
2. 增加 `body_xpath`参数，精确定义正文所在的位置，强力避免干扰。

例如对于澎湃新闻，在不设置`body_xpath`参数时：

```python
result = extractor.extract(html,
                           host='https://www.xxx.com',
                           noise_node_list=['//div[@class="comment-list"]',
                                            '//*[@style="display:none"]',
                                            '//div[@class="statement"]'
                                            ])
```

提取效果如下：

![](https://kingname-1257411235.cos.ap-chengdu.myqcloud.com/2020-06-06-11-51-44.png)

设置了`body_xpath`以后：

```python
result = extractor.extract(html,
                           host='https://www.xxx.com',
                           body_xpath='//div[@class="news_txt"]',  # 缩小正文提取范围
                           noise_node_list=['//div[@class="comment-list"]',
                                            '//*[@style="display:none"]',
                                            '//div[@class="statement"]'
                                            ])
```

结果如下：

![](https://kingname-1257411235.cos.ap-chengdu.myqcloud.com/2020-06-06-11-53-30.png)


## 0.1.9 (2020-03-11)

### Bug fix

1. 预处理可能会破坏 HTML 结构，导致用户自定义的 XPath 无法正确工作，因此需要把提取用户名、发布时间、标题的代码放在预处理之前。

## 0.1.8 (2020-02-21)

1. 感谢@止水提供的 meta 对应的新闻时间属性，现在会从 HTML 的 meta 数据中检查是否有发布时间。

## 0.1.7 (2020-02-13)

1. 在GeneralNewsExtractor().extract()方法中传入参数`author_xpath`和`publish_time_xpath`强行指定抓取作者与发布时间的位置。
2. 在.gne 配置文件中，通过如下两个配置分别指定作者与发布时间的 XPath

```yaml
author:
    xpath: //meta[@name="author"]/@content
publish_time:
    xpath: //em[@id="publish_time"]/text()
```

## 0.1.6 (2020-01-04)

1. 修复由于`node.getparent().remove()`会移除父标签中，位于自己后面的 text 的问题
2. 对于class 中含有`article`/`content`/`news_txt`/`post_text`的标签，增加权重
3. 使用更科学的方法移除无效标签

## 0.1.5 (2019-12-31)

通用参数可以通过 YAML、JSON 批量设置了。只需要在项目的根目录下创建一个 ``.gne`` ，就可以实现函数默认参数的功能。

## 0.1.4 (2019-12-29)

1. 现在可以通过传入参数`host`来把提取的图片url 拼接为绝对路径

例如：

```python
extractor = GeneralNewsExtractor()
result = extractor.extract(html,
                           host='https://www.xxx.com')
```

返回数据中：

```python
{
    ...
    "images": [
        "https://www.xxx.com/W020190918234243033577.jpg"
      ]
}
```

## 0.1.3 (2019-11-24)

1. 增加更多的 UselessAttr
2. 返回的结果包含`images`字段，里面的结果是一个列表，保存了正文中的所有图片 URL
3. 指定`with_body_html`参数，返回的数据中将会包含`body_html`字段，这是正文的 HTMl 源代码：

```python
...
result = GeneralNewsExtractor().extract(html, with_body_html=True)
body_html = result['body_html']
print(f'正文的网页源代码为：{body_html}')
```