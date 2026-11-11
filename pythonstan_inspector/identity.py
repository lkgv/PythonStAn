"""Finite, versioned alignment descriptions over frozen DTOs only.

Recipes enumerate audited native equality fields. Source definitions remain
conditional source-description candidates, never persistent native identities.
Display completeness alone does not admit an alignment candidate.
"""

MODEL = "kcfa_native_fields_v2"
SOURCE_CLASSES = frozenset((
    "IRModule", "IRFunc", "IRClass", "IRAstStmt", "IRContextEnter",
    "IRContextExit", "IRCatchException", "IRYield", "IRReturn", "IRRaise",
    "IRPass", "IRAwait", "IRDel", "IRImport", "IRCall", "IRAnno",
    "IRAssign", "IRCopy", "IRStoreAttr", "IRLoadAttr", "IRStoreSubscr",
    "IRLoadSubscr", "IRPhi"))
OBJECT_FIELDS = {
    "AbstractObject": ((), (), ()),
    "FunctionObject": ((), ("container_scope", "ir"), ()),
    "MethodObject": ((), ("container_scope", "ir", "class_obj"), ("instance_obj",)),
    "ClassObject": ((), ("container_scope", "ir"), ()),
    "ModuleObject": ((), ("ir",), ()),
    "InstanceObject": ((), ("class_obj",), ()),
    "ConstantObject": (("value",), (), ()),
    "BuiltinObject": ((), (), ()),
    "ListObject": ((), (), ()), "TupleObject": ((), (), ()),
    "MappingItemObject": ((), (), ()), "DictObject": ((), (), ()),
    "SetObject": ((), (), ()),
    "BuiltinClassObject": (("builtin_name",), (), ()),
    "BuiltinInstanceObject": (("builtin_type",), (), ()),
    "BuiltinMethodObject": (("method_name",), ("receiver",), ()),
    "BuiltinFunctionObject": (("function_name",), (), ()),
    "SuperObject": ((), (), ("current_class", "instance_obj")),
    "GeneratorObject": ((), ("func_obj", "container_scope"), ()),
    "CoroutineObject": ((), ("func_obj", "container_scope"), ()),
    "AsyncGeneratorObject": ((), ("func_obj", "container_scope"), ()),
}
CONTEXT_FIELDS = {
    "CallStringContext": (("k",), ("call_sites",)),
    "ObjectContext": (("depth",), ("alloc_sites",)),
    "TypeContext": (("depth",), ("types",)),
    "ReceiverContext": (("depth",), ("receivers",)),
    "ParamContext": (("depth",), ("params",)),
    "HybridContext": (("call_k", "obj_depth"), ("call_sites", "alloc_sites")),
}


def recipe(entity):
    """Return required scalar/link names, or refuse an unfamiliar description.

    Optional native None is affirmative scalar evidence. Absent links and
    omitted references cannot be substituted for None. Additional display
    links never extend these recipes.
    """
    attrs = entity.get("attributes", {})
    if attrs.get("identity_model") != MODEL or attrs.get("identity_coverage") != "complete":
        return None
    cls, kind = attrs.get("engine_class"), entity.get("kind")
    if type(cls) is not str:
        return None
    scalars, links = ["engine_class"], []

    def optional(name):
        flag = name + "_is_none"
        if type(attrs.get(flag)) is not bool:
            return False
        scalars.append(flag)
        if not attrs[flag]:
            links.append(name)
        return True

    if kind == "node" and cls == "NormalNode":
        links.append("variable")
    elif kind == "ctx" and cls == "Ctx":
        links.extend(("scope", "context"))
        content = attrs.get("content_kind")
        scalars.append("content_kind")
        if content == "variable":
            if (attrs.get("name_coverage") != "exact" or type(attrs.get("name")) is not str
                    or type(attrs.get("variable_kind")) is not str):
                return None
            scalars.extend(("name", "variable_kind"))
        elif content == "field":
            if (type(attrs.get("field_kind")) is not str
                    or any(attrs.get(name) is not None and type(attrs.get(name)) is not str
                           for name in ("field_name", "field_key_type"))
                    or attrs.get("field_index") is not None and type(attrs.get("field_index")) is not int):
                return None
            links.append("object")
            scalars.extend(("field_kind", "field_name", "field_index", "field_key_type"))
        elif content == "callsite":
            links.append("content")
        else:
            return None
    elif kind == "scope" and cls == "Scope":
        links.extend(("definition", "object", "context"))
    elif kind == "object" and cls in OBJECT_FIELDS:
        fields, required, maybe = OBJECT_FIELDS[cls]
        scalars.extend(fields)
        links.extend(("context", "allocation") + required)
        if not all(optional(name) for name in maybe):
            return None
        if cls == "BuiltinMethodObject":
            flag = attrs.get("receiver_var_is_none")
            if type(flag) is not bool:
                return None
            scalars.append("receiver_var_is_none")
            if not flag:
                if attrs.get("receiver_var_name_coverage") != "exact":
                    return None
                scalars.extend(("receiver_var_name", "receiver_var_kind"))
    elif kind == "context" and cls in CONTEXT_FIELDS:
        fields, sequences = CONTEXT_FIELDS[cls]
        if any(type(attrs.get(name)) is not int or not 0 <= attrs[name] <= 2**63 - 1 for name in fields):
            return None
        scalars.extend(fields)
        for sequence in sequences:
            count = attrs.get(sequence + "_length")
            if type(count) is not int or not 0 <= count <= 64:
                return None
            scalars.append(sequence + "_length")
            for index in range(count):
                name = f"{sequence}:{index}"
                is_tuple = attrs.get(name + "_is_tuple")
                if type(is_tuple) is not bool:
                    return None
                scalars.append(name + "_is_tuple")
                if not is_tuple:
                    links.append(name)
                    continue
                length = attrs.get(name + "_length")
                if type(length) is not int or not 0 <= length <= 64:
                    return None
                scalars.append(name + "_length")
                links.extend(f"{name}:{subindex}" for subindex in range(length))
    elif kind == "context" and cls == "SummaryContext":
        links.append("inner")
    elif kind == "context" and cls == "ProtocolKeyContext":
        links.extend(("inner", "partition"))
    elif kind == "context_partition" and cls == "KeywordPartition":
        if (attrs.get("key_kind") not in ("exact", "summary")
                or attrs.get("key_kind") == "exact" and type(attrs.get("key_value")) is not str
                or attrs.get("key_kind") == "summary" and attrs.get("key_value") is not None):
            return None
        scalars.extend(("key_kind", "key_value"))
        links.append("operation")
        if not optional("receiver_source"):
            return None
    elif kind == "origin" and cls == "CallSite":
        if (attrs.get("scope_name") is not None and type(attrs.get("scope_name")) is not str
                or attrs.get("index") is not None and type(attrs.get("index")) is not int):
            return None
        scalars.extend(("scope_name", "index"))
        links.append("statement")
    elif kind == "origin" and cls == "AllocSite":
        if attrs.get("token_coverage") == "opaque":
            return None
        scalars.append("alloc_kind")
        links.append("statement")
    elif kind == "origin" and cls in SOURCE_CLASSES:
        origin = entity.get("origin", {})
        if cls == "IRModule":
            if type(origin.get("file")) is not str or type(origin.get("qualname")) is not str:
                return None
        elif not all(type(origin.get(name)) is int for name in ("line", "column", "end_line", "end_column")):
            return None
    else:
        return None
    if any(name not in attrs for name in scalars):
        return None
    return tuple(scalars), tuple(links)
