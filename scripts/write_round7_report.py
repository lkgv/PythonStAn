#!/usr/bin/env python3
"""Render the validated round-7 whole-application report."""
from pathlib import Path
import json
r=Path(__file__).resolve().parents[1];e=r/'docs/certified_analysis/evidence'
s=json.loads((e/'round7_validation.json').read_text());f=s['full_repository'];m=s['configuration_projections']
rows=[]
for a in s['applications']:
 c=a['coverage'] or a.get('baseline_coverage') or {};q=a['current_calls'];value=f"{q['recovered_pairs']}/{q['required_pairs']}" if q['completed_call_projection'] else f"未完成，{q['unmeasured_required_pairs']} 项保留未测量"
 rows.append(f"| {a['id']} | {c.get('first_party_translated','?')} / {c.get('dependency_source_translated','?')} | {value} | {len(q['violated_negatives'])}（未测量 {q['unmeasured_negative_pairs']}） |")
text=f'''# PythonStAn 第七轮：完整应用分析与审计

本轮将工作单位提升为完整、未经改写的上游应用。新增项目级 API、隔离 CLI、框架入口及装饰器契约，并修复真实多模块分析发现的模块身份、导出、包导入和作用域错误。最终全仓为 **{f['passed']} passed、0 failed、0 errors、0 skipped**，保留 {f['warnings']} 条有意调用弃用 throw 签名产生的 warning。

目前输入包括 **两个完整小型上游应用**：Flask 3.1.2 官方 tutorial，以及 Pluggy eggsample 主程序与 spam 插件。后者分别测量仅第一方源码与加入真实兼容框架源码两种范围，因此共有三个场景，不能算三个独立应用。本轮尚未覆盖大型生产部署，也没有建立完整应用或任意请求的 soundness 证明。

基线提交 `e398666f9252d628c5d219804538fa3f623bd680`；最终核心指纹 `{s['implementation_sha256']}`。统一门禁、完整应用、旧基准和配置对照均绑定相同实现。实际运行观察在独立解释器生成，从未被转换为静态答案或自动生成 gold。

## 完整应用的最终结果

下表为 AI 开启模式；两模式的完整结果和差异见原始账本。源码覆盖指已翻译模块约束，函数体参与另有逐作用域记录。入口发现和声明的框架回调假设不计作已恢复的调用边。

| 应用／分析范围 | 第一方／依赖模块约束（超时场景取 AI 关闭诊断） | 必要调用恢复 | 负例违反 |
| --- | --- | --- | --- |
{chr(10).join(rows)}

Flask 原始项目完整保留 **22 个文件、4 个应用模块**，包括模板、SQL schema、静态文件、打包说明、许可及原始测试。实际 test client、CLI、请求上下文和 SQLite 场景 **17/17 检查通过**；未经修改的上游测试单独运行 **24 passed**。根分析器环境不安装 Flask，原始上游测试按显式目录边界从根收集中排除，其实际执行日志独立保存。七个真实运行依赖具有精确版本和下载 artifact hash。

Egssample 保留完整 host/plugin 原始源码：**5 个运行模块、2 个显式排除的构建脚本**。真实 Pluggy 0.13.1 满足其原始依赖约束，完整 **6 个框架模块**及许可、原始归档、分发元数据均已保存。实际执行原始 main、真实元数据发现、插件回调、共享调料表修改和输出，**7/7 检查通过**。两个范围重复同一运行事实，不增加独立样本数。

这两个应用共声明 25 个不同的调用要求；把同一插件应用的两种源码范围分别计入场景后，每种 AI 模式有 34 项分层要求。两种口径不能混用。未完成的求解保留全部正负分母，部分图只作诊断；不把超时或未测量解释为零漏报。

## 从完整应用中发现并修复的问题

项目引擎按 source_roots、import_roots 和 dependency_source_roots 建立规范模块名，在一个真实 PointerAnalysis arena 中分析独立模块。所有文件分别记录发现、解析、降级、约束翻译及调度；逐函数另记实际参与。不存在把文件拼成一个模块，或按 AST 函数名匹配来替代求解的路径。语法错误、重复模块身份、预算、源码变化和未解析依赖均保留状态与分母。

导入链修复包括共享模块单例、包父初始化、相对导入、同名子模块与真实包属性的区分、重导出、函数内导入和 GLOBAL 导出传播。此前模块读取还使用已经失效的 Variable 参数，真实跨模块属性访问会抛错。所有模块曾共用一份可变 import 列表，下一模块降级会清空旧模块记录；现在每个模块保存独立快照。模块元数据和 synthetic self 的词法模块／free-cell 也得到修复，避免不同模块的同名 callback 串流。

完整 Flask 首次只恢复 13/16 要求，遗漏集中在登录装饰器包装函数到原视图的调用。引擎改为初始化后对模块实际导出的 callable 添加真实 CallConstraint，保留返回 wrapper 和捕获的 view，而非仅激活同名源函数。Flask 路由与 functools.wraps 的 identity may-flow 使用绑定源码 hash、所属模块和原始 decorator index 的契约。已知属性修改、删除、别名污染或不匹配契约不能复用该身份。未知外层装饰器保持残差，不绕过已知替换。Click Command 的回调入口单独声明，Command 不被当成恒等函数返回值。两个求解阶段共享总迭代预算。

插件应用进一步暴露真正的反射断点：register(lib) 已有正确 ModuleObject，真实回调也在导出中，但未知名字的 getattr 返回了未连接的空单元。现已持续连接当前和后续真实导出。第一次修复又错误混入了为名称查找而注入的 builtins GLOBAL 变量，导致完整框架调用扩散、预算耗尽和超时；该失败已保留。本轮随后建立源码导出所有权，区分模块词法写入、真实模块别名写入／星号导入／包成员与 builtins 查找回退。显式 reexport 的 builtin 仍可导出，普通 len/getattr 等回退不自动成为模块属性。这条修复针对命名空间语义，不按基准回调名剪枝。项目模式随后默认关闭旧的自动 synthetic self 根，仅从声明入口和实际可达调用传播；底层旧模式保持默认兼容，实际方法调用仍保留。

## 框架边界与尚未解决的重难点

框架入口记录由真实 import/alias/constructor 关系推导，但仍是条件外部入口假设，不是注册可达性证明。外部参数见证尚未覆盖全部参数形状及初始堆；nested/class 入口、任意装饰器、未知 app 参数、元数据加载和 native 效果仍保留义务。synthetic method context 的存在也不能解释为具体执行可达。

Egssample 的另一首个断点在 metadata.distributions／ep.load：静态分析尚未把分发元数据中的目标连接为 eggsample_spam 的 ModuleObject。即使整个插件文件已被解析，其函数也不会自动加入注册表。这是下一轮的明确算法目标：用版本／hash 绑定的分发元数据解析建立目标模块关系，并保留其他安装插件、导入 hooks 和加载效果的未知补集。不能通过直接添加 _multicall 到预期回调的边来消除这个缺口。

完整框架压力场景仍未通过：AI 关闭在 50,000 次总迭代后未收敛，部分图已包含库插件两项回调；AI 开启触发 120 秒墙钟限制。因此该场景的九项正例和两项负例全部保留未完成，部分图的 7/9 不能算成功。完整框架源码模式更能暴露模型和扩展性问题；源码全部翻译与最终不动点是不同指标。当前受限入口、框架契约及 native 依赖不能支持任意请求、环境或完整 Python 的 soundness 定理。此前三十项闭合效果仍在独立通道验证，不会因项目分析存在而变成一般应用证明。

## 配置、回归与证据

新的项目对照覆盖两个第一方应用范围的 0/1/2-CFA、AI 关闭／开启，每格两次；完整依赖压力场景单独保留 AI 关闭／开启的有界测量，避免机械重复已知超时：**{m['trials']} 次运行、{m['configuration_rows']} 行配置**。其中 **{m['completed_trials']} 次完成，{m['stable_rows']} 行调用投影重复稳定，{m['rows_meeting_reviewed_calls']} 行满足全部调用及入口要求**。未满足的要求保持研究门禁失败；重复配置不是独立应用。

旧十二场景仍按原 gold 验证，两模式均 84/84 必要调用、11/23 负例违反；58 个冻结文件逐字节不变。闭合效果通道仍是原 30/30 条件验证，一般 Python／KCFA 效果证明仍为 0/30。全仓通过数不代表同等数量的语言特性或普遍证明。

原始记录包括固定旧提交的导入测试失败、早期项目计数适配问题、包装器断链、包含 builtin 回退的错误导出模型及被终止的旧配置测量。开发结果和最终指纹明确区分，不以更好看的旧结果替代发布结果。

复现入口：

```sh
python scripts/analyze_project.py --config benchmark/round7_apps/configs/flask_official_tutorial_3_1_2.json --output project.json
python -m benchmark.round7_apps.runner --web-python .runtime-web/bin/python --plugin-python .runtime-plugin/bin/python --output apps.json
python scripts/check_application_configurations.py --output app_configurations.json
python scripts/run_analysis_evaluation.py --full-suite
```

运行环境准备见 `benchmark/round7_apps/web/README.md` 和 `nonweb/README.md`。插件应用与其兼容依赖已完整保存；Web 环境依赖按 runtime_requirements.txt 安装。项目 CLI 通过独立进程施加墙钟与地址空间限制，错误和超时保留分母。研究门禁未满足时命令返回 2。

最终机器账本：[round7_validation.json](evidence/round7_validation.json)。架构见 `round7_architecture.md`；六份专题报告为 `round7_project_engine.md`、`round7_imports_audit.md`、`round7_frameworks_audit.md`、`round7_formal_audit.md`、`round7_web_audit.md`、`round7_nonweb_audit.md`。本轮没有在线 LLM 调用，也没有远程仓库操作。
'''
text=text.replace('Egssample','Eggsample')
(r/'docs/certified_analysis/ROUND7_REPORT_ZH.md').write_text(text)
print({'report_chars':len(text),'full_tests':f['passed']})
