<pre>
<font color="deepskyblue" > ______            _                    </font><font color="black">_                      </font>
<font color="deepskyblue">(_____ \      _   | |                  </font><font color="black">| |   _      </font><font color="red">/\         </font>
<font color="deepskyblue"> _____) _   _| |_ | | _   ___  ____</font><font color="black">     \ \ | |_</font><font color="red">   /  \  ____  </font>
<font color="deepskyblue">|  ____| | | |  _)| || \ / _ \|  _ \</font><font color="black">     \ \|  _) </font><font color="red">/ /\ \|  _ \ </font>
<font color="yellow">| |    | |_| | |__| | | | |_| | | | |</font><font color="black">_____) | |__</font><font color="red">| |__| | | | |</font>
<font color="yellow">|_|     \__  |\___|_| |_|\___/|_| |_</font><font color="black">(______/ \___</font><font color="red">|______|_| |_|</font>
<font color="yellow">       (____/                                                  </font>

</pre>

PythonStAn is yet another **Python** **St**atic **An**alysis framework.

Latest checkpoint: [round 14 implementation and empirical report](docs/certified_analysis/ROUND14_REPORT_ZH.md), [inspector usage](docs/inspector/README_ZH.md), [explicit keyword-context audit](docs/certified_analysis/ROUND14_KEY_CORRELATION_AUDIT.md), and [14-project protocol](docs/certified_analysis/ROUND14_PROJECT_PROTOCOL.md). Offline inspector answers are about sealed snapshots. Passing local regressions or the module-solver/report gate does not establish full Python soundness, arbitrary effects or end-to-end defensive properties. The report retains incomplete analyses, failed timing gates and explicit remaining obligations.

Previous algorithm repair and evaluation: [round 11 report](docs/certified_analysis/ROUND11_REPORT.md) and [architecture decisions](docs/certified_analysis/round11_architecture.md).

Earlier checkpoints: [round 10 report](docs/certified_analysis/ROUND10_REPORT.md), [round 9 report](docs/certified_analysis/ROUND9_REPORT.md), [round 7 report](docs/certified_analysis/ROUND7_REPORT_ZH.md), [configuration contracts](docs/certified_analysis/round2_algorithm_config.md), and [empirical web slices](docs/certified_analysis/round2_empirical.md).

The `codex/certified-dynamic-analysis` development branch adds an independently
checked source-summary pass and repairs several KCFA/abstract-interpretation
propagation issues. Start with the [implementation and audit report](docs/certified_analysis/AUDIT_REPORT_ZH.md)
or the [usage guide](docs/certified_analysis/README.md). The checked source
fragment is intentionally limited; full dynamic Python and web applications
remain open work. Historical test migrations, real semantic repairs and remaining proof obligations are reported separately.

Run the full offline evaluation with `python scripts/run_analysis_evaluation.py`. The component-only gate is `python scripts/check_certified_release.py`; neither gate establishes full Python soundness.

---


## TODO List:
* IR:
  - [DONE] Three-address transformer
  - [DONE] CFG generator
  - SSA transformer

* Analysis:
  - [DONE] Dataflow analysis
    - liveness analysis
    - reaching definition analysis
  - [DONE] Closure analysis
  - Interprocedual analysis
  - Anderson's pointer analysis
  - Type Analysis
