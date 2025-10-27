"""Tests for module summary architecture.

Tests dependency graph, summary extraction/application, and multi-module analysis.
"""

import pytest
from pythonstan.analysis.pointer.kcfa.dependency_graph import ModuleDependencyGraph
from pythonstan.analysis.pointer.kcfa.module_summary import (
    ModuleSummary, FunctionSummary, ClassSummary
)
from pythonstan.analysis.pointer.kcfa.state import PointsToSet
from pythonstan.analysis.pointer.kcfa.object import AllocSite, AllocKind


class TestDependencyGraph:
    """Test module dependency graph."""
    
    def test_add_import(self):
        """Test adding import edges."""
        graph = ModuleDependencyGraph()
        graph.add_import("main", "utils")
        graph.add_import("utils", "base")
        
        assert "utils" in graph.get_imports("main")
        assert "base" in graph.get_imports("utils")
        assert "main" in graph.get_importers("utils")
        assert "utils" in graph.get_importers("base")
    
    def test_topological_sort_simple(self):
        """Test topological sort with simple dependency chain."""
        graph = ModuleDependencyGraph()
        graph.add_import("main", "utils")
        graph.add_import("utils", "base")
        
        ordered = graph.topological_sort()
        
        assert ordered.index("base") < ordered.index("utils")
        assert ordered.index("utils") < ordered.index("main")
    
    def test_topological_sort_diamond(self):
        """Test topological sort with diamond dependency."""
        graph = ModuleDependencyGraph()
        graph.add_import("main", "left")
        graph.add_import("main", "right")
        graph.add_import("left", "base")
        graph.add_import("right", "base")
        
        ordered = graph.topological_sort()
        
        base_idx = ordered.index("base")
        left_idx = ordered.index("left")
        right_idx = ordered.index("right")
        main_idx = ordered.index("main")
        
        assert base_idx < left_idx
        assert base_idx < right_idx
        assert left_idx < main_idx
        assert right_idx < main_idx
    
    def test_detect_cycles_no_cycle(self):
        """Test cycle detection with no cycles."""
        graph = ModuleDependencyGraph()
        graph.add_import("main", "utils")
        graph.add_import("utils", "base")
        
        cycles = graph.detect_cycles()
        assert len(cycles) == 0
    
    def test_detect_cycles_simple_cycle(self):
        """Test cycle detection with simple cycle."""
        graph = ModuleDependencyGraph()
        graph.add_import("a", "b")
        graph.add_import("b", "a")
        
        cycles = graph.detect_cycles()
        assert len(cycles) == 1
        assert set(cycles[0]) == {"a", "b"}
    
    def test_detect_cycles_complex(self):
        """Test cycle detection with multiple modules."""
        graph = ModuleDependencyGraph()
        graph.add_import("a", "b")
        graph.add_import("b", "c")
        graph.add_import("c", "a")
        
        cycles = graph.detect_cycles()
        assert len(cycles) == 1
        assert set(cycles[0]) == {"a", "b", "c"}
    
    def test_resolve_relative_import_same_package(self):
        """Test relative import resolution in same package."""
        graph = ModuleDependencyGraph()
        
        result = graph.resolve_relative_import("pkg.sub.mod", "foo", 1)
        assert result == "pkg.sub.foo"
    
    def test_resolve_relative_import_parent_package(self):
        """Test relative import resolution to parent package."""
        graph = ModuleDependencyGraph()
        
        result = graph.resolve_relative_import("pkg.sub.mod", "foo", 2)
        assert result == "pkg.foo"
    
    def test_resolve_relative_import_empty_name(self):
        """Test relative import with empty module name."""
        graph = ModuleDependencyGraph()
        
        result = graph.resolve_relative_import("pkg.sub.mod", "", 2)
        assert result == "pkg"
    
    def test_resolve_relative_import_root(self):
        """Test relative import to package root."""
        graph = ModuleDependencyGraph()
        
        result = graph.resolve_relative_import("pkg.sub.mod", "x", 3)
        assert result == "x"


class TestModuleSummary:
    """Test module summary data structures."""
    
    def test_function_summary_creation(self):
        """Test creating function summary."""
        summary = FunctionSummary(
            name="foo",
            params=("x", "y"),
            context_returns={"ctx1": PointsToSet.empty()},
            param_effects={}
        )
        
        assert summary.name == "foo"
        assert summary.params == ("x", "y")
        assert "ctx1" in summary.context_returns
    
    def test_function_summary_merge(self):
        """Test merging function summaries."""
        alloc1 = AllocSite(file="f1", line=1, col=0, kind=AllocKind.OBJECT)
        alloc2 = AllocSite(file="f2", line=2, col=0, kind=AllocKind.OBJECT)
        
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        
        ctx = CallStringContext(call_sites=(), k=2)
        obj1 = AbstractObject(alloc_site=alloc1, context=ctx)
        obj2 = AbstractObject(alloc_site=alloc2, context=ctx)
        
        s1 = FunctionSummary(
            name="foo",
            params=("x",),
            context_returns={"ctx1": PointsToSet.singleton(obj1)},
            param_effects={}
        )
        
        s2 = FunctionSummary(
            name="foo",
            params=("x",),
            context_returns={"ctx1": PointsToSet.singleton(obj2)},
            param_effects={}
        )
        
        merged = s1.merge(s2)
        
        assert merged.name == "foo"
        assert len(merged.context_returns["ctx1"]) == 2
    
    def test_class_summary_creation(self):
        """Test creating class summary."""
        alloc = AllocSite(file="f", line=1, col=0, kind=AllocKind.CLASS, name="MyClass")
        
        summary = ClassSummary(
            name="MyClass",
            alloc_site=alloc,
            bases=("Base1", "Base2"),
            methods={},
            attributes={}
        )
        
        assert summary.name == "MyClass"
        assert summary.bases == ("Base1", "Base2")
        assert summary.alloc_site == alloc
    
    def test_module_summary_creation(self):
        """Test creating module summary."""
        summary = ModuleSummary(
            module_name="mymodule",
            exports={},
            functions={},
            classes={},
            context_map={},
            visible_allocs=frozenset(),
            external_calls=frozenset()
        )
        
        assert summary.module_name == "mymodule"
        assert len(summary.exports) == 0
    
    def test_module_summary_empty(self):
        """Test creating empty module summary."""
        summary = ModuleSummary.empty("test")
        
        assert summary.module_name == "test"
        assert len(summary.exports) == 0
        assert len(summary.functions) == 0
        assert len(summary.classes) == 0
    
    def test_module_summary_merge(self):
        """Test merging module summaries."""
        alloc = AllocSite(file="f", line=1, col=0, kind=AllocKind.OBJECT)
        
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        
        ctx = CallStringContext(call_sites=(), k=2)
        obj = AbstractObject(alloc_site=alloc, context=ctx)
        
        s1 = ModuleSummary(
            module_name="test",
            exports={"x": PointsToSet.singleton(obj)},
            functions={},
            classes={},
            context_map={},
            visible_allocs=frozenset(),
            external_calls=frozenset()
        )
        
        s2 = ModuleSummary(
            module_name="test",
            exports={"y": PointsToSet.singleton(obj)},
            functions={},
            classes={},
            context_map={},
            visible_allocs=frozenset(),
            external_calls=frozenset()
        )
        
        merged = s1.merge(s2)
        
        assert merged.module_name == "test"
        assert "x" in merged.exports
        assert "y" in merged.exports
    
    def test_module_summary_get_export_names(self):
        """Test getting all export names."""
        func_summary = FunctionSummary(name="foo", params=())
        class_alloc = AllocSite(file="f", line=1, col=0, kind=AllocKind.CLASS)
        class_summary = ClassSummary(name="Bar", alloc_site=class_alloc)
        
        summary = ModuleSummary(
            module_name="test",
            exports={"x": PointsToSet.empty()},
            functions={"foo": func_summary},
            classes={"Bar": class_summary},
            context_map={},
            visible_allocs=frozenset(),
            external_calls=frozenset()
        )
        
        names = summary.get_export_names()
        assert names == {"x", "foo", "Bar"}


class TestSummaryExtraction:
    """Test summary extraction from state."""
    
    def test_export_summary_empty(self):
        """Test exporting empty summary."""
        from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
        
        state = PointerAnalysisState()
        summary = state.export_summary("test_module", set())
        
        assert summary.module_name == "test_module"
        assert len(summary.exports) == 0
    
    def test_export_summary_with_exports(self):
        """Test exporting summary with variables."""
        from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
        from pythonstan.analysis.pointer.kcfa.variable import Variable, Scope, VariableKind
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject
        
        state = PointerAnalysisState()
        
        ctx = CallStringContext(call_sites=(), k=2)
        scope = Scope(name="test_module", kind="module")
        var = Variable(name="x", scope=scope, context=ctx, kind=VariableKind.GLOBAL)
        
        alloc = AllocSite(file="f", line=1, col=0, kind=AllocKind.OBJECT)
        obj = AbstractObject(alloc_site=alloc, context=ctx)
        
        state.set_points_to(var, PointsToSet.singleton(obj))
        
        summary = state.export_summary("test_module", {"x"})
        
        assert "x" in summary.exports
        assert len(summary.exports["x"]) == 1


class TestSummaryApplication:
    """Test applying summaries to state."""
    
    def test_import_summary_basic(self):
        """Test importing summary into state."""
        from pythonstan.analysis.pointer.kcfa.state import PointerAnalysisState
        from pythonstan.analysis.pointer.kcfa.context import CallStringContext
        from pythonstan.analysis.pointer.kcfa.object import AbstractObject
        
        state = PointerAnalysisState()
        ctx = CallStringContext(call_sites=(), k=2)
        
        alloc = AllocSite(file="f", line=1, col=0, kind=AllocKind.OBJECT)
        obj = AbstractObject(alloc_site=alloc, context=ctx)
        
        summary = ModuleSummary(
            module_name="imported",
            exports={"foo": PointsToSet.singleton(obj)},
            functions={},
            classes={},
            context_map={},
            visible_allocs=frozenset(),
            external_calls=frozenset()
        )
        
        state.import_summary(summary, "imported", ctx)
        
        from pythonstan.analysis.pointer.kcfa.variable import Variable, Scope, VariableKind
        scope = Scope(name="imported", kind="module")
        var = Variable(name="foo", scope=scope, context=ctx, kind=VariableKind.GLOBAL)
        
        pts = state.get_points_to(var)
        assert len(pts) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

