"""Compiler-style class-private identifiers for the owned AST execution model.

String literals and keyword-call labels stay literal. Declared display names
remain available separately from the compiler's mangled binding names.
"""
import ast


def mangle(private, name):
    if not private or not name or not name.startswith('__') or name.endswith('__') or '.' in name:return name
    prefix=private.lstrip('_')
    return '_'+prefix+name if prefix else name


def lower_private_names(tree):
    if getattr(tree,'_private_names_lowered',False):return tree
    pending=[(tree,None)]
    while pending:
        node,private=pending.pop()
        if isinstance(node,ast.ClassDef):
            original=node.name;changed=mangle(private,original)
            if changed!=original:node._source_declared_name=original;node.name=changed
            # Header expressions are evaluated in the enclosing private scope;
            # the body starts the class's own lexical private scope.
            pending.extend((child,private) for child in [*node.decorator_list,*node.bases,*node.keywords,*getattr(node,'type_params',())])
            pending.extend((child,original) for child in node.body)
            continue
        # Preserve the compiler's original annotation text before identifiers
        # in that expression are lowered. Future annotations store that text.
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            annotations = [arg.annotation for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
            annotations.extend([node.returns, node.args.vararg.annotation if node.args.vararg else None, node.args.kwarg.annotation if node.args.kwarg else None])
            for annotation in annotations:
                if annotation is not None and not hasattr(annotation, '_source_annotation_text'):
                    annotation._source_annotation_text = ast.unparse(annotation)
        elif isinstance(node, ast.AnnAssign) and not hasattr(node.annotation, '_source_annotation_text'):
            node.annotation._source_annotation_text = ast.unparse(node.annotation)
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            original=node.name;changed=mangle(private,original)
            if changed!=original:node._source_declared_name=original;node.name=changed
        if isinstance(node,ast.Name):node.id=mangle(private,node.id)
        elif isinstance(node,ast.Attribute):node.attr=mangle(private,node.attr)
        elif isinstance(node,ast.arg):node.arg=mangle(private,node.arg)
        elif isinstance(node,(ast.Global,ast.Nonlocal)):node.names=[mangle(private,name) for name in node.names]
        elif isinstance(node,ast.ExceptHandler) and node.name:node.name=mangle(private,node.name)
        elif isinstance(node,(ast.MatchAs,ast.MatchStar)) and node.name:node.name=mangle(private,node.name)
        elif isinstance(node,ast.MatchMapping) and node.rest:node.rest=mangle(private,node.rest)
        elif isinstance(node,ast.Import):
            for alias in node.names:
                alias.name=mangle(private,alias.name)
                if alias.asname:alias.asname=mangle(private,alias.asname)
        elif isinstance(node,ast.ImportFrom):
            if node.module:node.module=mangle(private,node.module)
            for alias in node.names:
                alias.name=mangle(private,alias.name)
                if alias.asname:alias.asname=mangle(private,alias.asname)
        pending.extend((child,private) for child in ast.iter_child_nodes(node))
    tree._private_names_lowered=True
    return tree
