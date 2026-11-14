# PythonStAn 后续开发执行计划（2026-11-14）

## 当前起点与证据边界

基线为 `df5bed989f062e4e785f5732aec7369da0760e67`，tree `ee7e09e3ecefabf5c612bd45f4f70103c1d95bc8`，目标分支为 `dev-cocktail-analysis`。

本计划对照了《PythonStAn_Next_Development_Agent_Prompt_v2_ZH.txt》全文、Inspector v2 增补，以及该提交的源码、任务板、gate 和客户查询协议。它细化后续执行，不替代原 43 卡或原 46 卡验收，也不以旧任务板状态推断当前实现完成度。

当前执行环境不可用。本次完成的是远端源码与需求核对、任务拆分和文档结构校验；生产代码未修改，运行测试全部为 `not_run`。旧的未推送诊断材料不可假定仍可读取，需从冻结输入重建。

基线提交消息记录的历史结果为 audit 5222 pass / 2 fail、stage 1046 pass / 1 fail、mandatory 36/36、semantic 49/52、LV 3/6，以及质量零新增/零修改诊断。它们不是本次重新执行的结果，也不表示全仓严格类型、整体 soundness 或发布验收完成。旧质量摘要和任务板中的更早结果同样只作为历史。

机器可读切片、依赖、写入范围与验收见 [continuation_tasks_20261114.json](continuation_tasks_20261114.json)。原 [task_board.json](task_board.json) 的状态保持原样；最终 verified 仍必须满足其原依赖和 [gate_manifest.json](gate_manifest.json)。

## 立即恢复与首次执行

R0 恢复可执行环境后，先核对实际 HEAD/tree、dirty diff、模块加载路径、Python、Poetry 与锁定工具身份；保留较新改动。当前远端可能已增加本计划提交，应区分代码基线与执行提交身份。不要把重建环境误当作重置源代码的许可。

按原 Poetry lock 恢复依赖，不顺带升级 Ruff 0.17.4、mypy 2.4.1、Import Linter 2.16。从仓库及原交接输入重建冻结 source/config/query/model 身份，再执行：
- mandatory、原阶段集合、audit、完整 semantic/LV manifest；
- 质量全扫描、strict frontier、依赖和负例；
- 每个待修项的独立红例、正控制和实际成本记录。

当前所有这些执行检查均未运行。历史测试数量不是新的通过门。普通语义 witness 的原资源与正式 cohort 的资源分别保持，不能混用：例如冻结递归见证的 10000 work / 2 s / 512 MB 与原 cohort S 的 10000 / 8 s / 1536 MiB / outer 15 s 属于不同协议。

STATE01/02、SEM03 等未整卡 verified 时，可在 owner 冻结的 `interface_ready` 合同上做有界开发；最终验收不能跳过硬依赖。作者与独立 reviewer 分工，所有共享生产文件先分配 lease。

## 第一条生产线：调用边界、递归关联与固定点

当前源码仍让普通函数从 `store.clear_effects()` 接收完整状态；`Store.apply` 导入缺失 cells 并应用 CellWrite。同 key 递归还可能复用 local CellId。另一方面，同 key 输入 join/widen 后，当前调用复用该 key 的全部 outcomes。这是两个须分别定位、分别验收的机制。

### A-FRAME

修改点为 `driver.request_function`、`Store.apply`、Frame/Unit 边界及对应 state transform。区分 caller continuation 私有 cells 与 globals、closure/captured cells、bound arguments、defaults、namespace 和 suspended continuation 可达状态。

不能直接删除全部 locals 再恢复快照。返回 closure 的 escaping cell 可能与 caller 私有 cell 同 ID，恢复快照可能覆盖合法共享写入。需要明确有限 cell allocation/summary 或保守 shared/havoc 回退；未来可能捕获的 cells 也不能因当前 read-set 小而丢弃。未知 alias 扩大共享 footprint。

先用非逃逸局部隔离、共享写回、递归逃逸 closure、defaults/nonlocal/finally/generator 等独立判据建立边界。隔离成功不能关闭全部递归精度义务。

### A-CORRELATION-DESIGN / IMPL

冻结 heap 例不依赖 sum 例的私有 local/r；单做帧隔离不足以恢复它。合并入口后重复应用出口可能产生 `X = {1} ∪ (X+1)` 的抽象方程；此处是源码机制推导，恢复运行后仍需以实际 entry/exit 轨迹验证。

先比较两个有限方案：
1. 参数分区并带覆盖 overflow：复用现有 Unit/Flow，限制 token 数，全部准备与求解计费。相同 n、不同 box 输入仍可能合并；必须检查连续调用 box 0→5→10、分区溢出和多参数情况。
2. 受检 guarded/relational summary：需要输入 guard 覆盖与残余、完整 completion/effect、alias/fresh/havoc 语义。给 poststate 增加 guard 字段不足以建立输入→输出关系。

设计审查后才领取最小实现 lease。禁止整个 Store/hash 入 CallKey、无界 specialization、仅增 context 深度或提高预算。

建议在现有 Unit 记录中明确 entry revision 与 evaluated-entry revision：入口增长推进版本，求解期间重入增长不能让旧结果续签 coverage。entry、outcomes、completion/effects、coverage、policy、epoch、footprint 改变均唤醒消费者；值不变也可能有 coverage 进展。复用现有 SummaryDependency 与 ProcedureObservationStore，不新增第二份摘要库存。

sum=10、heap=5、context0 的有限非 Top 覆盖、常量互递归及旧正例均须原 profile 重验；随后完成原 AI02 的 SCC/WTO、iterator/loop 方程、widen/narrow 和后不动点验收，才进入 AI03 程序点增量。先前按 producer 重算整个 caller 可以保留，不能永久冒充 AI03 完成。

## 第二条生产线：Native 有序调用准备

局部 B-PLAN/B-ORDER/B-REPLAY 对应 whole-source Native 的调用准备与原 SEM/PTA03 守卫；原 PTA01 的 region 顺序 checkpoint、PTA02 的 repeated allocation/基数仍是独立义务，不合并声明。

复用已有 `pythonstan/analysis/semantics/callplan.py`：它区分 positional starred/deferred、连续 named-keyword group 和 **mapping；AI 已实际消费。当前 Native 链的断点是：
- `ThreeAddressTransformer.visit_Call` 提前把表达式块放进外层；
- IRCall 缺少受前阶段正常完成约束的表达式块；
- translator 丢 starred 标志并将 keywords 转成 frozenset；
- 末端 CallConstraint guard 无法撤销已经发布的表达式 effects。

**B-PLAN**：共享计划贯穿 TAC、IRCall、translator 和真实消费者，保留重复 **operand、rename/copy/loads 与 helper 薄适配。不得只新增闲置 DTO；该节点不能宣称旧顺序问题已经修好。

**B-ORDER**：在表达式块、star materialization、keyword group/mapping merge、binding 与 invoke 间建立单调阶段屏障。named group 与 sole-star 延迟按共享 Python 3.12 计划执行，不能按 lineno 排序或统一把全部 star 提前。

exact 子集需要覆盖当前 operand alternatives 的完整 shape/provenance 见证。Native mapping 的 may-key/field 关系不能证明 arity、顺序、key absence。空 points-to 是 pending/unknown，不是空参数。确定失败分支停止自己的后缀；mixed/unknown 分支保留正常、异常、callback、heap/global/cell effects 并保持 Open。不能删已发布 inclusion edges。

Native 的 exception/control routing 仍有残余，调用内阶段修复不能提升成整个 caught/finally 精确保证；Native region 与 AI 的出口合同另验。

**B-REPLAY**：复用现有 dynamic constraint、old-fact replay、future subscriptions 和四队列调度。不得新增未计账 callback 循环或隐藏队列。阶段 ID 有限，不含无界 replay 序号、完整 Store 或嵌套 caller 链。

恢复后先重建独立 CPython 3.12 矩阵，再改生产：sole-star、多 star、文本 keyword-before-star、连续 named group、duplicate/nonstr 拒绝时机、custom keys/getitem/iterator 中途 raise、binder/default/posonly/kwonly；保留前缀、late-arrival、future-effect、预算中断和 19 context/独立 heap 正控。旧“14 oracle、4 RED”只属历史信息，不冒充本次或已恢复证据。

## 资源、查询与后续链路

C-SEM04 保留两个 callback normal-value coverage 失败的原 gold/assertions/caps。分开测量求解、状态复制、导出成本，不能以 unavailable/Open、免费遍历或提高限额消红。它与 AI 共享 Driver/Transfer，生产修改需串行 lease。

C-COST-CONTRACT 重建 Native max_work=2 的旧合同与最小成本见证。不能静默把 backend iteration budget 改称 global fuel，也不能改 cost factor 或让 setup/export 免费。若合同不相容，明确保留 contract_decision_open，单独评审规范和全部消费者迁移。

后续按原依赖推进：
- Q1 的单项 LV02 通过不关闭多调用点事实、摘要原子性、缓存失效与 L1 query-quality/cost 义务；完整 query 向量必须保留。
- HYB01 实现独立 source callable constraints、arg→param、return→result、late-callee replay 及实际 AI 消费。它仍是 Partial may oracle；D 禁 Native 预跑，E 禁隐式 whole-source 前置，不盲目求交。
- AI03 需要 AI02，完成一个真实程序点增量路径并报告同质量完整成本；再收敛 CTRL02、ARCH01、OBS01、TYPE02、有限 TCB 独立审计。
- REL01 冻结唯一 wheel/source，checkout 外安装消费者通过后进行正式评估和最终复核。

## 正式评估与条件 live

以 [query_manifest.json](query_manifest.json)、[legacy_compatibility.json](client_queries/legacy_compatibility.json) 和 [protocol.json](client_queries/protocol.json) 为准。原 18×5=90 行、失败分母、同 inventory/entry/input/query/model、完整成本和可比性保持；Inspector 的 14 项目诊断复验不能替代正式 cohort。

指定完整项目 mandatory query 是 `invoke_command_names:q1`，不能替换成更简单的查询。六条真实 query、切片/项目矩阵以及至少一个规定完整项目的 closed-useful 验收均保留。

Live smoke 要等 AUDIT01、REL01、EVAL02；held-out 再等 EVAL03，真实切片再按数据与收益条件推进。当前六个客户 case 的 `allow_external_llm=false`；源码公开不等于已允许外发。费用未知保留 reservation 并停止后续收费。当前没有真实模型调用。

G8 分阶段条件状态独立报告，不能无条件阻断 G9 核心交付；也不能用此例外豁免 G0–G7 的内部正确性、类型或 mandatory 客户查询失败。

## 提交与恢复纪律

重要节点及时推送；持续工作期间至多间隔一小时检查并推送已审查的变更，无变更不制造空提交。只推源码、合成测试、公共配置及工程文档；原始日志、私有输入、内部审查记录、凭据和不透明归档保留在公开提交之外。

每次先读取实际远端 HEAD，用 expected-sha 非强制更新 `dev-cocktail-analysis`，main 保持不动。回读 commit/tree；所有未运行检查保留 not_run。不得将本计划提交当作生产修复、重新验收或项目完成。

## R0 的远端执行恢复

本地环境仍不可用，但原质量工作流已对计划提交 `f7e835b` 成功运行（[run 43580412078](https://github.com/lkgv/PythonStAn/actions/runs/43580412078)）。据此添加 `continuation-baseline.yml`，在 GitHub CI 直接运行 mandatory、原 stage、audit 和完整 semantic/LV 四组冻结检查。

该工作流沿用已锁定工具、Python 3.12 与既有 checkout/setup 步骤。四组各有独立退出状态，fail-fast 关闭以保留完整分母，不使用 continue-on-error、xfail 或已知失败豁免。20 分钟仅是外层任务看门狗，不修改分析器的任何资源 profile。它不读取模型凭据或执行 live 请求。

每组打印实际 commit、命令、Python、源码 digest、完整原始输出、exit code、耗时和前后源码稳定性；semantic 输出完整结构化回执。新四组结果在执行前仍为 pending/not_run，原质量成功不代替这四组验收。远端 CI 的通过/失败必须在该提交身份下单独记录；它也不表示本地交互式环境已恢复。
