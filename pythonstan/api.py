"""Public API; every input and command enters the same AnalysisSession."""
from .inputs import Source, File, Project
from .results import AnalysisResult, OutputSpec, ValueQuery, PropertyQuery, QueryAnswer
from .config import AnalysisConfig, ConfigurationError
from .session import AnalysisSession


def analyze(target, *, config=None, overrides=None, queries=None, output=None):
    with AnalysisSession(target, config=config, overrides=overrides, queries=queries, output=output) as session:
        return session.execute()


__all__ = ["analyze", "Source", "File", "Project", "OutputSpec", "ValueQuery", "PropertyQuery",
           "AnalysisResult", "QueryAnswer", "AnalysisConfig", "ConfigurationError", "AnalysisSession"]
