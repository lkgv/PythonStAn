from typing import Dict, List
from pythonstan.analysis import Analysis, AnalysisConfig

class AnalysisManager:
    prev_analysis: Dict[AnalysisConfig, List[AnalysisConfig]]
    next_analysis: Dict[AnalysisConfig, List[AnalysisConfig]]