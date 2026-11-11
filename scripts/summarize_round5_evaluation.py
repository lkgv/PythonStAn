#!/usr/bin/env python3
"""Validate and summarize the frozen round-5 evidence; refuses stale implementation hashes."""
from pathlib import Path
import json,re,hashlib,sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from scripts.run_analysis_evaluation import fingerprint
p=root/'docs/certified_analysis/evidence'
def read(name):return json.loads((p/name).read_text())
cycle=read('round5_cycle/summary.json'); hard=read('round5_hardcases.json'); matrix=read('round5_config_matrix.json'); copy=read('round5_copy_config_matrix.json'); diff=read('round5_hardcases_differential.json'); closed=read('round5_cycle/closed_heap_effects.json'); hold=read('round5_holdouts.json'); oracle=read('round5_cycle/exception_oracle.json'); proj=read('round5_config_audit.json'); copyproj=read('round5_copy_config_audit.json'); migration=read('round5_test_migration_audit.json')
sha=fingerprint()
for item in (cycle,hard,matrix,copy):
 assert item['implementation_stable'] and item['implementation_sha256_before']==item['implementation_sha256_after']==sha
assert cycle['gates_passed'] and diff['no_reviewed_regressions']
assert all(not item['failed_checks'] and not item['unmeasured_checks'] for item in (proj,copyproj))
full=(p/'round5_cycle/historical_full_suite.log').read_text(); match=re.search(r'^(\d+) passed in ([\d.]+)s$',full,re.M); assert match
count=int(match[1]); scoped=int(re.search(r'^(\d+) passed in ',(p/'round5_cycle/scoped_gate.log').read_text(),re.M)[1])
assert migration['phase']=='final'
assert read('round5_frozen_corpus_integrity.json')['all_unchanged']
files=['round5_cycle/summary.json','round5_cycle/historical_full_suite.log','round5_hardcases.json','round5_hardcases_differential.json','round5_config_matrix.json','round5_config_audit.json','round5_copy_config_matrix.json','round5_copy_config_audit.json','round5_cycle/closed_heap_effects.json','round5_holdouts.json','round5_cycle/exception_oracle.json','round5_generator_protocol.json','round5_test_migration_audit.json','round5_frozen_corpus_integrity.json']
summary={'schema_version':1,'base_commit':'d9cc0e6108b490135ef57b5597bfd3962c3e4d15','implementation_sha256':sha,'python':'3.12.14','full_repository':{'passed':count,'failed':0,'errors':0,'skipped':0,'wall_seconds':float(match[2])},'scoped_tests_passed':scoped,'hardcases':hard['totals'],'reviewed_regressions':diff['totals'],'six_project_configurations':matrix['totals'],'dict_copy_configurations':copy['totals'],'projected_inclusion_checks':proj['total_checks']+copyproj['total_checks'],'conditional_effects':closed['summary'],'full_python_soundness_proved':False,'global_acceptance_achieved':False,'all_frozen_gold_unchanged':True,'methodology':'Test API migrations are explicit; passes do not establish universal semantic soundness. Conditional source-model effects are not KCFA or unrestricted proofs. Configuration trials and overlapping test suites are not independent samples.','evidence':[{'path':name,'sha256':hashlib.sha256((p/name).read_bytes()).hexdigest()} for name in files]}
(p/'round5_validation.json').write_text(json.dumps(summary,indent=2)+'\n')
text=f'''# PythonStAn 第五轮审计与修复报告

本轮已消除历史全仓测试的失败和错误，并补齐上一轮缺失的两项调用关系。最终统一验收为 **{count} passed、0 failed、0 errors、0 skipped**；冻结的六项目十二场景在 AI 关闭和开启两种模式下均恢复 **84/84** 项必要调用。**全局 soundness 目标仍未达到**：23 项指定负例仍违反 11 项，KCFA 对原 30 项效果仍未给出静态证明。新增闭合堆模型完成其中 **14 项条件验证**，其余 **16 项仍为该模型的残差**；这个条件计数不能改写为“全局已证明 14 项”。

基线提交为 `d9cc0e6108b490135ef57b5597bfd3962c3e4d15`，最终核心实现指纹为 `{sha}`。统一门禁、十二场景、六项目配置矩阵和额外复制矩阵的前后指纹完全一致。环境为 Python 3.12.14。全部结果离线生成，本轮未调用 LLM API，也未执行远程仓库操作。

## 验收结果及正确解释

| 指标 | 第四轮 | 第五轮 |
| --- | --- | --- |
| 全仓测试 | 1409 passed、234 failed、27 errors、25 skipped | {count} passed、0 failed、0 errors、0 skipped |
| 冻结必要调用关系，每种 AI 模式 | 82/84 | 84/84 |
| 指定负例违反，每种 AI 模式 | 11/23 | 11/23 |
| 同时满足全部调用正负要求的场景 | 6/12 | 7/12 |
| KCFA 静态效果证明 | 0/30 | 0/30 |
| 独立闭合堆模型的条件效果验证 | 无此通道 | 14/30；16 项残差 |
| 全 Python／完整 Web 应用 soundness | 未建立 | 未建立 |

全仓计数包括以前因 collection error 未能执行的测试、修复后的旧测试，以及新回归。不能把通过数增加解释为同等数量的新语言功能。旧接口迁移、原 skeleton 的过时预期、真实算法缺陷和新增负例都有独立记录；最终迁移账本覆盖 {len(migration['files'])} 个测试文件，记录原／新断言、异常预期、decorator 和 skip。它统计的是定义，不等同于 pytest 参数展开后的用例数。没有以批量 xfail 或跳过失败来清除红灯；CLI 的失败即跳过和八个旧功能占位 skip 都已改为实际执行的功能或残差契约检查。

原十二场景的全部 **58 个文件**与基线逐字节一致，包括源码、gold、manifest、上游原始片段和许可记录。AI 两模式分别新增恢复同样的两项关系，因此差分文件按 24 个 case-mode 行计为新增 4 项；不能说发现了四条不同关系。没有丢失既有必要关系，也没有新增指定负例违反。runtime 验证仍为 12/12，但 runtime 通过不构成静态效果证明。

## 算法修复及审计闭环

第一组修复建立了通用的 own namespace 与 mapping 关系。原实例字段读取会混入继承属性，不能直接拿来代表 `__dict__`。现在 raw own storage 与逻辑属性读取分离，实时字典视图只连接 own 字段，并双向传播后续写入；复制映射有独立身份，只沿 source→copy 传播。`items()` 的每个有限 key 使用独立的二位置 tuple，避免在刚生成元素时就丢掉键值关联。前端同时保留全部 `**` 操作数，参数绑定持续订阅命名形参和 rest 字典。这条通用链修复了 SQLAlchemy hybrid `_copy` 的两项遗漏，不依赖框架名称或向 gold 人工补边。流不敏感复制仍可能包含复制后的源写入，comprehension 的局部 join 仍可能失去跨键关系，这些不是已解决的 snapshot 精度。

第二组修复由更精确的值域反过来揭示前端错误。有限 builtin 整数运算与比较、布尔分支排除、返回常量地址见证、序列长度和 `len` 使真实值流可见；无界整数区间错误归一化为 bottom 也被修复。随后发现旧 `while` 条件被提到循环外，只计算一次。新的可执行 AST 每轮重算 guard，保留 continue、break 和 else，包括内层 else 中作用于外层循环的跳转。`for` 改用独立 sentinel，不再吞掉真实 None，并在 guard 推进 iterator。常量表区分 bool/int/float/complex 和浮点 signed zero。原 AST 与降级 AST 的独立 Python 对照检验保留在新测试中。

迭代语义仍有边界：被源码遮蔽的 `iter/next` 和 async-for 同步降级并未完整解决。现在不可变 frontend residual 经 AST→IR→PTA/AI 传递，关闭 unknown diagnostics 也不能将这些路径误报为完整。最后一个旧 iterator skip 又暴露自定义 `__iter__/__next__` 的真实漏边；本轮补上类型层特殊方法查找、实际 receiver、晚到类字段、返回回调和无目标调用的副作用。实例同名属性不能劫持隐式协议，耗尽、descriptor、getitem fallback 和状态推进仍保留残差。

第三组修复针对公共查询、作用域和不动点机制。模块 GLOBAL 被错误查询为 LOCAL、AI driver 把整数 converged=0 当成已收敛、NormalNode 包装导致模块 seed 全部漏读、约束多触发器登记／移除不一致、默认参数定义环境错误等都有直接回归。MRO 查询现在来自真实的 lexical base 关系：仅当有序 base 槽各有一个已知类时使用 C3；多候选、动态聚合、未知或空显式 metaclass 保留候选与残差。live 投影不缓存为精确快照。新的元类 `__call__` 反例在固定十二场景已全部恢复之后才被发现，随后再次解冻修复；持久元类订阅恢复了返回 callback→19 的路径，同时保留普通构造 may 分支与优先级义务。修复前后报告都已保存。

模块与 CLI 也得到实际修复。ModuleDependencyGraph 支持确定性 SCC 调度；ModuleAnalyzer 使用真实已 lowering 的闭合模块和生产求解器，明确拒绝不完整 import summary 替代真实模块；binding projection 始终不是完整效果摘要。原 AI benchmark 命令事实上未运行 AI，现在真正运行 KCFA→AI，并用独立 worker 落实硬超时。配置、IR/CFG 导出、预算不足和输入错误均有真实测试；`--widening` 同时设置普通与循环 widening 延迟，历史字段选项的别名映射有明确说明。

## 框架效果与生成器：新增能力的边界

闭合堆检查器直接从原始 AST 推导状态与效果，不执行目标源码、不读取 runtime trace 来充当证明。候选必须绑定源码 hash 和语义版本，并按递归类型严格相等比较最终观察值。预算耗尽、未支持转移或异常不允许输出 verified。独立审查发现并修复 mapping view、range 类型、class mappingproxy、嵌套类作用域、动态活动异常及 setter-only descriptor 优先级错误，之后新增普通类 `__set_name__` 的顺序语义。

| 冻结场景 | 条件验证效果 |
| --- | ---: |
| SQLAlchemy hybrid dual dispatch | 3 |
| SQLAlchemy hybrid copy modifiers | 4 |
| Click parent object decorator | 2 |
| Click ensure object factory | 3 |
| Pydantic descriptor proxy | 2 |
| 其余七个场景 | 0；保留 16 项残差 |

这些结果只覆盖指定闭合入口和已声明的受限语义，不能直接作为任意请求／任意堆的框架关系摘要，也不用于剪掉 KCFA 候选边。其 Python 对应性有反例与差分证据，但没有机械化语义证明。原 30 项 unrestricted/global 义务仍全部开放。

生成器模块使用源绑定、模型签发的不可变 continuation，区分 yield/return/raise/close，保存跨 finally yield 的 pending return/exception，支持 next/send/throw/close 和本次恢复的调用者异常上下文。伪造或跨模型 handle 被拒绝，预算耗尽返回 unknown。独立 Python 对照覆盖 **2744 条三步轨迹、8232 次操作**，以及更深 cleanup、初生 throw、PEP 479 和忽略 close 等边界。它支持有限不可变值片段；一般共享堆、yield from、async 和完整 Pluggy wrapper 调度仍需联合模型，当前通用 generator 摘要没有因新模块存在而自动变完整。

## 配置与经验验证

原六项目矩阵保持第四轮预选的六个场景，检查 0/1/2-CFA × index off/on × AI off/on，每格重复两次，共 **{matrix['totals']['rows']} 行、{matrix['totals']['trials']} 次运行**；{matrix['totals']['completed_trials']} 次完成、{matrix['totals']['stable_rows']} 行调用投影稳定，{matrix['totals']['fully_satisfied_rows']}/72 行满足全部正负调用要求。仍不满足的行保留误报，不能用“求解完成”替代“精度通过”。

为直接检验本轮主修复，额外增加 SQLAlchemy copy 的相同 12 配置、24 次运行；**24/24 完成、12/12 行稳定且通过全部 10 条必要调用和 1 条负例**。主矩阵与复制矩阵的 **{proj['total_checks']+copyproj['total_checks']} 项有限调用投影 inclusion 检查**全部通过。这不证明任意程序上的配置单调性，更不是不同 abstract object identity 的集合包含定理。

既有 development cohort 仍为 26/26 必要调用、2/5 负例违反；既有四项 holdout 仍为 28/28 必要调用、0/5 负例违反。旧纯异常 oracle 的 1000 个生成程序、4000 次具体执行均被受限源模型覆盖。新的生成器轨迹和反例参与过开发，不被包装成未见过的 holdout。所有框架实验都是固定版本真实方法片段及显式适配边界，完整 Web 应用分析数仍为 0。运行时间来自并行验收期间的隔离进程，仅作描述性数据，不提出加速或统计显著性结论。

## 仍需收敛的关键义务

剩余 11 项指定负例集中在 FastAPI 的 override/cache 关联、Pluggy 的 first-result/wrapper 行为和 Click 的对象选择。接下来需要保留 callable identity、key、use_cache、存储结果、异常／暂停状态之间的关系，才能在可验证 guard 下排除候选。单纯补 may-edge 已经不足以解决这些精度问题。

闭合效果向一般框架摘要迁移，还必须建立输入覆盖、heap alias/cardinality、正常／异常／暂停结果以及 residual 补集的模拟关系。生成器需要与共享 heap 和框架调度联合；class construction 仍有 prepare/new/init、descriptor/subclass hooks 的完整次序；映射仍缺时间、存在性和跨 key 关系。未知语义的日志也不等于真实的保守 heap havoc 或 callback 转移。没有这些义务，不能因为全仓变绿就声称最初的全局 soundness 目标已经完成。

## 复现与证据索引

```sh
python scripts/run_analysis_evaluation.py --full-suite
python scripts/run_realworld_hardcases.py --output docs/certified_analysis/evidence/local_hardcases.json
python -m benchmark.realworld_hardcases.differential --before docs/certified_analysis/evidence/round4_hardcases.json --after docs/certified_analysis/evidence/local_hardcases.json --output docs/certified_analysis/evidence/local_difference.json
python -m benchmark.realworld_hardcases.config_matrix --output docs/certified_analysis/evidence/local_matrix.json
python -m benchmark.realworld_hardcases.config_matrix --case sqlalchemy_hybrid_copy_modifiers --output docs/certified_analysis/evidence/local_copy_matrix.json
python scripts/check_closed_heap_effects.py --output docs/certified_analysis/evidence/local_closed_effects.json
python scripts/do_generator_protocol.py --help
python scripts/audit_test_migrations.py --phase final
```

统一门禁返回 0；十二场景主命令及六项目矩阵仍因指定负例返回 2，非零结果原样保留。复制矩阵返回 0。`--allow-analysis-gaps` 仅影响基础设施命令退出码，不改变数据。较早不稳定指纹、真实失败和修复前反例都在 `evidence/round5_intermediate/` 及各专家记录中，不能当作最终实现的测量。

主账本为 [round5_validation.json](evidence/round5_validation.json)，含最终证据 hash。设计见 [round5_architecture.md](round5_architecture.md)。专题审计分别为 `round5_formal_audit.md`、`round5_heap_mapping_audit.md`、`round5_kcfa_compat_audit.md`、`round5_ai_compat_audit.md`、`round5_framework_effects_audit.md`、`round5_generator_audit.md`、`round5_frontend_protocol_residuals.md`、`round5_iterator_builtin_audit.md` 和 `round5_ai_benchmark_cli_audit.md`。本轮未新增真实 LLM 联调；历史四次 API 证据仍属于更早的 v1，不能替代当前模块的验证。
'''
(root/'docs/certified_analysis/ROUND5_REPORT_ZH.md').write_text(text)
print(json.dumps({'full_tests':count,'scoped_tests':scoped,'fingerprint':sha,'matrix':matrix['totals'],'copy':copy['totals'],'report_chars':len(text)}))
