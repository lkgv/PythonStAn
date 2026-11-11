#!/usr/bin/env python3
"""Render the round-6 report from its validated final ledger."""
from pathlib import Path
import json
r=Path(__file__).resolve().parents[1];p=r/'docs/certified_analysis/evidence'
s=json.loads((p/'round6_validation.json').read_text());m=s['six_project_configurations'];c=s['dict_copy_configurations'];f=s['full_repository'];h=s['hardcases']['current'];n=s['new_compositions'];q=n['calls']['current']
text=f'''# PythonStAn 第六轮审计、修复与验收报告

本轮将原十二场景的闭合效果条件验证从 **14/30 推进到 30/30**，并完成生成器恢复与共享堆、原生协程 await 树、类构造钩子和有限输入关系的实现。最终全仓为 **{f['passed']} passed、0 failed、0 errors、0 skipped**，保留一次有意测试弃用 throw 签名产生的 DeprecationWarning。原调用基准仍为 **84/84 必要关系、11/23 负例违反**，没有通过不安全剪枝换取精度数字。

**全局 soundness 目标仍未达到。** 30/30 表示指定源码、指定入口和受限操作语义下的静态条件验证；原 30 项一般 Python 效果证明义务仍全部开放，KCFA 效果证明仍为 0/30。模型与完整 Python 语义之间没有机械化对应证明，完整 Web 应用和整包静态分析数仍为 0。

基线为 `52668d52ab22821c907496ccfb8086b0d76691bb`。最终核心实现指纹为 `{s['implementation_sha256']}`。全仓门禁、原场景、新组合场景和两个配置矩阵均绑定同一实现，前后指纹一致。环境为 Python 3.12.14、pytest 7.4.4、PyYAML 6.0.3、Pluggy 1.7.0。本轮未使用在线 LLM API，也未执行远程仓库操作。

## 最终验收

| 维度 | 第五轮 | 第六轮及解释 |
| --- | --- | --- |
| 全仓测试 | 2210 passed | {f['passed']} passed；0 failed/errors/skipped，1 warning |
| 原十二场景必要调用，每种 AI 模式 | 84/84 | 84/84；无新增缺失 |
| 原十二场景负例违反 | 11/23 | 11/23；7/12 场景满足完整调用正负要求 |
| 原三十项闭合效果 | 14 条件验证、16 残留 | 30 条件验证、该固定入口集合无残留 |
| 原三十项一般 Python／KCFA 效果证明 | 0/30 | 仍为 0/30 |
| 新四个组合场景 | 无 | 37/37 必要调用、{q['violated_negatives']}/8 负例违反、11/11 条件效果 |
| 有限输入关系 | 无 | 8/8 输入组合完整验证，域外和其他初始堆不覆盖 |
| 六项目配置矩阵 | 144 次完成、72 行稳定 | {m['completed_trials']}/{m['trials']} 次完成、{m['stable_rows']}/{m['rows']} 行稳定；{m['fully_satisfied_rows']}/{m['rows']} 行满足全部调用要求 |
| 复制场景追加矩阵 | 24 次完成、12 行稳定且精度通过 | {c['completed_trials']}/{c['trials']} 次完成、{c['stable_rows']}/{c['rows']} 行稳定；{c['fully_satisfied_rows']}/{c['rows']} 行精度通过 |

两个矩阵共 **{s['projected_inclusion_checks']} 项有限调用投影包含检查**通过。这些是实际有限场景的配置验证，不能推广成任意程序上所有配置的单调性证明。原十二场景的 **58 个冻结文件**逐字节保持不变，包含源码、manifest、gold、原始上游片段和许可。全部运行保留未完成与残差分母；新基准没有并入旧基准以改变旧分数。

原 corpus 的 required/negative 差分在两模式合计 24 行上没有新增缺失或新增负例违反。随着更多协议路径被展开、未知返回补集被记录，残差记录数由 731 增至 {h['reported_residual_obligations']}，unknown 记录分别为 {s['hardcases']['baseline']['unknown_records']} 和 {h['unknown_records']}。这些是重复上下文下的记录量，不能解释为相同数量的独立缺陷，也不能因为报告数量更多而掩盖未完成语义。

## 算法与实现：真正补上的部分

共享堆生成器复用闭合模型的表达式、别名、字典、列表、调用和异常语义，通过显式任务栈保存循环、处理器、finally 与 pending return/raise。next、send、throw、close 推进同一个堆，跨暂停的事件写入不会被重放或丢失。两个未改动 Pluggy 场景现在能推导完整事件顺序和结果，包括异常注入、恢复、first-result 包装及清理。组件差分验证包含 2744 条三步轨迹、8232 次操作，以及 1024 条八步轨迹、8192 次操作，每步比较共享堆效果。它们参与了开发，不能作为未见过的外部 holdout。

原生协程创建时绑定参数，进入 await/send 后才执行函数体；嵌套 await 在同一闭合堆上工作，保持回调身份和缓存关系。模型检查重复 await、重入、newborn send/close/throw 和 StopIteration 转换，并拒绝外部 awaitable、调度与取消。FastAPI manifest 原本就声明了 `await_global: pending`，本轮将这一入口显式纳入静态模型，并为有序 driver 单独计算 hash。候选必须同时绑定源码、语义版本与入口；创建一个尚未执行的协程不再被误当成完成整个请求。两个 FastAPI 场景的覆盖、缓存复用与绕过次数因此可在声明条件下验证。

闭合堆 v2 增加普通元类选择、命名空间、new/init、返回类身份、属性优先级、默认对象身份、普通异常子类和列表原位扩展。独立审计随后发现并修复 comprehension 局部作用域、异常处理器元组校验、异常选择器的活动异常、私有名改写、隐式类元数据、返回对象实际元类初始化、异常子类属性、描述符回退、浮点符号零和不可变 tuple 身份等错误。对旧提交重放的七个反例曾能验证错误候选；新回归要求正确完成或明确残留，不能输出另一个完成结果。额外的正例防止实现靠一律 unsupported 通过验收。

生产 KCFA 也补上了独立的 may-flow。真值测试持续订阅类型层 __bool__/__len__，普通与反射运算订阅实际源方法，保留 receiver、返回回调、晚到字段和无目标调用的副作用。实例同名属性不能劫持隐式协议。类语句自己的命名空间进一步订阅普通 __set_name__，显式元类 new/init 得到 name、bases、namespace 和返回值传播；闭包身份得到保留。构造器与绑定方法的临时变量原来仅按源坐标命名，合成 TAC 的重复位置使左右操作数意外串流，现已加入真实 IR 语句及接收对象身份区分。独立反射运算测试直接检查 self={{Right}}、other={{Left}}，并恢复四组严格负例断言。

这些生产修复由泛化的源语义关系驱动，不依赖框架名或 gold。普通／反射候选在优先级未知时都保留；任意 descriptor、继承元类选择、prepare、动态 type 的 set_name、subclass hooks 和精确时序仍是残差。闭合模型能计算某个场景，不会自动使生产 KCFA 的对应路径获得完备性证书。

## 被审计否决的优化

本轮曾实现单调的延迟分支约束：按已知标量真值激活分支，空集合在不动点尾部放开两侧。这使 Click 的一项误报消失，却被独立反例否决。`flag=False; flag=1+1; if flag: yes()` 中，若第二个生产者没有贡献值，集合虽非空，却只剩旧 False。用它排除分支会漏掉实际 yes 调用。`not False` 也暴露相同问题。

我们撤回了这项自动剪枝，保留代码实验、失败日志和原有 11 项负例。只修补算术空洞不能解决未建模调用、属性、下标和隐式返回的完整性。下一版必须有独立的生产者覆盖域，证明用于排除分支的值集合覆盖全部行为；约束添加的单调性本身不够。本轮没有留下一个默认开启或未经验证的实验配置。

## 从单入口计算到有限关系契约

新增 finite_entry 层覆盖显式声明的有限参数笛卡尔积，每行重新执行源码初始化，建立输入→返回值及全局效果的关系。示例穷举 override、handled failure、route 的八个组合，保留相关性；布尔、整数、浮点和符号零都有严格类型区别。候选必须覆盖完整行集合，缺行、换行、替换源码／输入域、混入不完整行都无法验证整个 family。合法 100×100 输入域即使超过枚举预算，也保留已知分母 10,000，而不是把未测量数量记作 0。

这个接口只对声明的有限输入、源码初始化堆和一次入口调用量化。它没有覆盖任意初始堆、域外输入或同一堆上的连续请求；也没有给 KCFA 授权剪枝。LLM 将来可以生成候选，但不能选择需要被验证的子集、提供可信堆事实或豁免未知分支。本轮提交的候选还经过独立 CPython 见证检查，生产计算不消费这些运行结果。

## 新基准的质量与边界

新四个场景分别组合 Django 两次分发与接收者变更、Click 上下文重绑定和异常清理、SQLAlchemy 兄弟复制与后续源修改、Pluggy 三次分发及包装器移除。上游方法保持原样，新的边界适配和手写要求在测量前固定；四个定向语义突变会被 gold 拒绝。最终运行验证 4/4，通过 37 条必要调用，仍有 {q['violated_negatives']} 条负例违反，仅 {q['useful_reviewed_cases']}/4 场景满足全部调用正负要求。开发阶段曾报告 3 条负例，其中一条因撤回不安全 guard 优化恢复；最终以冻结后的 4 条为准。

另有实际调用 Pluggy 1.7.0 PluginManager 进行注册、注销和分发的独立运行见证。它与冻结的 1.6.0 方法片段版本不同，报告记录版本和源码 hash，不能冒充相同版本整包静态覆盖。所有组合场景仍是方法／协议切片，非完整 Web 应用；新数据也没有统计独立性或跨框架普遍迁移的证明。各角色的测试数与配置重复有重叠，不能相加作独立样本规模。验收并行运行期间的耗时只作描述。

## 下一轮必须解决的义务

首先，需要建立生产者覆盖与 unknown 补集的可组合规则，使真正完整的值关系能够安全指导分支和框架特化；残差日志本身不等于 heap havoc 或完整 callback transfer。其次，需要将有限关系契约提升到有明确输入和堆前置条件的抽象关系摘要，证明 guard、别名、对象基数、正常／异常／暂停结果和 fallback 的对应关系。FastAPI 与 Pluggy 的十项关系误报以及 Click 的一项选择误报仍不能靠补 may-edge 消除。

生成器还缺 delegation、部分嵌套暂停、一般终结语义，协程仍缺外部调度、取消及并发；类构造仍缺上述通用动态路径。若没有这些桥接证明，原 30 项 global/unrestricted 义务仍不能关闭。经验验证方面还需要真实整项目模块导入、框架初始化和请求入口联合分析；当前整应用计数保持 0。

## 复现与文件索引

```sh
python scripts/run_analysis_evaluation.py --full-suite --output-dir docs/certified_analysis/evidence/local_cycle
python scripts/run_realworld_hardcases.py --output docs/certified_analysis/evidence/local_hardcases.json
python -m benchmark.round6_realworld.runner --output docs/certified_analysis/evidence/local_compositions.json
python -m benchmark.realworld_hardcases.config_matrix --output docs/certified_analysis/evidence/local_matrix.json
python -m benchmark.realworld_hardcases.config_matrix --case sqlalchemy_hybrid_copy_modifiers --output docs/certified_analysis/evidence/local_copy_matrix.json
python scripts/check_closed_heap_effects.py --output docs/certified_analysis/evidence/local_closed_effects.json
python scripts/check_finite_entry.py --output docs/certified_analysis/evidence/local_finite_entry.json
```

原／新调用基准和主配置矩阵仍会因负例返回 2；不能把这些研究缺口吞成通过。源码证书、有限契约与统一工程门禁的结果单独报告。最终账本 [round6_validation.json](evidence/round6_validation.json) 绑定全部主要证据 hash；`scripts/summarize_round6_evaluation.py` 会拒绝过期实现指纹。

设计见 [round6_architecture.md](round6_architecture.md)。专题报告为 `round6_effects_audit.md`、`round6_generator_audit.md`、`round6_coroutine_audit.md`、`round6_precision_audit.md`、`round6_construction_audit.md`、`round6_formal_audit.md`、`round6_composition_audit.md`、`round6_empirical_audit.md`。测试迁移账本逐项记录旧契约修改；本轮生产指针旧测试最终保持原义，仅闭合效果测试将已实现语义由 unsupported 改为精确正例，并保留新的真实残差负例。没有用跳过、xfail 或改写冻结 gold 清理失败。
'''
(r/'docs/certified_analysis/ROUND6_REPORT_ZH.md').write_text(text)
print({'report_chars':len(text),'full_passed':f['passed']})
