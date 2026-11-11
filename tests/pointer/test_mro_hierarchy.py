"""C3 and reverse-edge obligations through the current object-based public API."""
import pytest
from tests.pointer.kcfa.builders import make_scope
from pythonstan.analysis.pointer.kcfa.class_hierarchy import ClassHierarchyManager, MROError


def build_hierarchy(parents):
    objects = {name: make_scope(name, "class").obj for name in parents}
    hierarchy = ClassHierarchyManager()
    for name, bases in parents.items():
        hierarchy.add_class(objects[name], [objects[base] for base in bases])
    return hierarchy, objects


def names(objects):
    return [obj.ir.name for obj in objects]


def test_c3_mro_simple_inheritance():
    hierarchy, classes = build_hierarchy({"object": [], "Animal": ["object"], "Dog": ["Animal"]})
    assert names(hierarchy.get_mro(classes["Dog"])) == ["Dog", "Animal", "object"]


def test_c3_mro_diamond_inheritance():
    hierarchy, classes = build_hierarchy({"object": [], "A": ["object"], "B": ["A"], "C": ["A"], "D": ["B", "C"]})
    assert names(hierarchy.get_mro(classes["D"])) == ["D", "B", "C", "A", "object"]


def test_c3_mro_complex_multiple_inheritance():
    hierarchy, classes = build_hierarchy({"object": [], "A": ["object"], "B": ["object"], "C": ["A"], "D": ["B"], "E": ["C", "D"]})
    assert names(hierarchy.get_mro(classes["E"])) == ["E", "C", "A", "D", "B", "object"]


def test_hierarchy_bases_tracking():
    hierarchy, classes = build_hierarchy({"Animal": [], "Mammal": ["Animal"], "Dog": ["Mammal"]})
    assert hierarchy.get_bases(classes["Dog"]) == [classes["Mammal"]]
    assert hierarchy.get_bases(classes["Mammal"]) == [classes["Animal"]]
    assert hierarchy.get_bases(classes["Animal"]) == []


def test_hierarchy_subclasses_tracking():
    hierarchy, classes = build_hierarchy({"Animal": [], "Dog": ["Animal"], "Cat": ["Animal"]})
    assert set(hierarchy.get_subclasses(classes["Animal"])) == {classes["Dog"], classes["Cat"]}


def test_mro_caching():
    hierarchy, classes = build_hierarchy({"A": [], "B": ["A"]})
    assert hierarchy.get_mro(classes["B"]) is hierarchy.get_mro(classes["B"])


def test_mro_cache_invalidation():
    hierarchy, classes = build_hierarchy({"A": [], "B": ["A"], "C": ["B"], "Other": []})
    before = hierarchy.get_mro(classes["C"])
    hierarchy.update_bases(classes["B"], [classes["Other"]])
    after = hierarchy.get_mro(classes["C"])
    assert names(before) == ["C", "B", "A"]
    assert names(after) == ["C", "B", "Other"]
    assert after is not before
    assert classes["B"] not in hierarchy.get_subclasses(classes["A"])
    assert hierarchy.get_subclasses(classes["Other"]) == [classes["B"]]


def test_inconsistent_c3_order_is_an_error():
    hierarchy, classes = build_hierarchy({"X": [], "Y": [], "A": ["X", "Y"], "B": ["Y", "X"], "C": ["A", "B"]})
    with pytest.raises(MROError):
        hierarchy.get_mro(classes["C"])
    assert classes["C"] not in hierarchy._mro_cache


def test_inheritance_cycle_is_an_error_and_can_be_repaired():
    hierarchy, classes = build_hierarchy({"A": [], "B": ["A"]})
    hierarchy.update_bases(classes["A"], [classes["B"]])
    with pytest.raises(MROError, match="Cyclic"):
        hierarchy.get_mro(classes["A"])
    hierarchy.update_bases(classes["A"], [])
    assert names(hierarchy.get_mro(classes["B"])) == ["B", "A"]
