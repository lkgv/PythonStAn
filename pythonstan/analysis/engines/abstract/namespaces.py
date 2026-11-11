"""Store-backed Python namespaces and key protocol dispatch.

Namespace views contain no copied authority: globals and object dictionaries read
and write the current Store. Function locals are explicit fast-locals snapshots.
"""
from __future__ import annotations
from dataclasses import replace
from itertools import product
from pythonstan.analysis.domains.values import Value
from .state import HeapObject,Flow


class NamespaceOperations:
    def __init__(self,transfer):self.t=transfer;self.d=transfer.d;self.ops=transfer.ops
    def _private(self,key):
        from pythonstan.analysis.contracts.private_slots import is_private_slot
        return is_private_slot(key)
    def _current(self,store):
        if hasattr(store,'ensure_current'):store.ensure_current(self.ops)
    def _havoc(self,obj,store):
        owner=store.heap.get(obj.metadata.get('owner'))
        return obj.havoc or bool(owner and owner.havoc) or (obj.metadata.get('namespace_kind')=='globals' and obj.metadata.get('module') in store.havoc_namespaces)
    def _metadata(self,module):
        source=self.d.modules[module]
        import ast
        tree=self.d.trees[module]
        return {'__name__':module,'__file__':source.filename,
                '__package__':module if source.is_package else module.rpartition('.')[0],
                '__doc__':ast.get_docstring(tree,clean=False)}
    def _seed_module(self,module,store):
        if module not in self.d.modules or module not in self.d.trees:
            # A dotted external import can have a parent module identity
            # without source. Its namespace is unknown, never a closed empty
            # mapping or fabricated local filename/package/doc metadata.
            store.havoc_namespaces|=frozenset((module,))
            self.d.open('external_module_namespace:'+module,store)
            return
        if module in store.havoc_namespaces:return
        for name,value in self._metadata(module).items():
            cell=f'global:{module}:{name}'
            if cell not in store.cells:store.write_cell(cell,Value.constant(value))
    def global_view(self,module,store):
        self._seed_module(module,store)
        identity=self.d.allocate_identity('namespace:globals:'+module)
        if identity not in store.heap:store.heap[identity]=HeapObject('namespace',singleton=identity=='namespace:globals:'+module,metadata={'namespace_kind':'globals','module':module})
        return Value.ref(identity)
    def local_view(self,frame,store):
        if frame.is_module:return self.global_view(frame.module,store)
        prefix='local:'+frame.identity+':'
        original='namespace:locals:'+frame.identity
        identity=self.d.allocate_identity(original)
        if frame.is_class:
            if identity not in store.heap:store.heap[identity]=HeapObject('namespace',singleton=identity==original,metadata={'namespace_kind':'class_locals','prefix':prefix})
        else:
            # Refresh only actual fast-local/free-variable entries. User-added
            # keys survive synchronization, just as in CPython3.12.
            local_cells={name:frame.cell(name) for name in frame.locals}
            local_cells.update(dict(frame.closure))
            previous=store.heap.get(identity)
            fields=dict(previous.fields) if previous else {}
            missing=set(previous.maybe_missing) if previous else set()
            for name,cell in local_cells.items():
                value=store.effective_cell(cell)
                if value.without_unbound().is_bottom():fields.pop(name,None);missing.add(name)
                else:
                    fields[name]=value.without_unbound()
                    if value.unbound:missing.add(name)
                    else:missing.discard(name)
            marker=('heap_meta',identity,'locals_refresh')
            fresh_activation=previous is not None and marker not in store.writes
            current=HeapObject('namespace',fields,maybe_missing=missing,singleton=identity==original and not fresh_activation,metadata={'namespace_kind':'locals_snapshot'})
            if previous and (not previous.singleton or fresh_activation):
                current=previous.join(current,self.ops);current.singleton=False
            store.heap[identity]=current
            store.writes.add(marker)
            for name in (set(previous.fields) if previous else set())|set(fields):store.writes.add(('heap',identity,name))
        return Value.ref(identity)
    def object_view(self,store,owner,readonly=False):
        owner=self.ops.arena.resolve(owner)
        original='namespace:object:'+owner
        identity=self.d.allocate_identity(original)
        if identity not in store.heap:store.heap[identity]=HeapObject('namespace',singleton=identity==original,metadata={'namespace_kind':'object','owner':owner,'readonly':readonly})
        return Value.ref(identity)
    def bindings(self,obj,store):
        mode=obj.metadata.get('namespace_kind')
        if mode=='globals':
            module=obj.metadata['module'];self._seed_module(module,store);prefix='global:'+module+':'
            return {**obj.fields,**{cell[len(prefix):]:value.without_unbound() for cell,value in store.cells.items()
                    if cell.startswith(prefix) and not value.without_unbound().is_bottom()}}
        if mode=='class_locals':
            prefix=obj.metadata['prefix']
            return {**obj.fields,**{cell[len(prefix):]:value.without_unbound() for cell,value in store.cells.items()
                    if cell.startswith(prefix) and not value.without_unbound().is_bottom()}}
        if mode=='object':
            owner=store.heap.get(obj.metadata['owner'])
            if owner is None:return {}
            return {key:value for key,value in owner.fields.items() if not self._private(key)}
        return {key:value for key,value in obj.fields.items() if not self._private(key)}
    def _read(self,obj,key,store):
        if self._private(key):return Value.bottom(),True
        mode=obj.metadata.get('namespace_kind')
        if mode=='globals':
            if type(key) is not str:
                return obj.fields.get(key,obj.default),key not in obj.fields or key in obj.maybe_missing
            self._seed_module(obj.metadata['module'],store)
            value=store.effective_cell('global:'+obj.metadata['module']+':'+key)
            return value.without_unbound(),value.unbound
        if mode=='class_locals' and type(key) is str:
            value=store.effective_cell(obj.metadata['prefix']+key)
            return value.without_unbound(),value.unbound
        if mode=='object':
            owner=obj.metadata['owner'];target=store.heap.get(owner)
            if target is None:return Value.unknown(),True
            value,missing=store.read_slot(owner,key,self.ops)
            return value,missing
        return store.read_slot(next((ref for ref,current in store.heap.items() if current is obj),''),key,self.ops)
    def _write(self,identifier,key,value,store,*,weak=False):
        obj=store.heap[identifier];mode=obj.metadata.get('namespace_kind')
        if self._private(key):return
        if mode=='globals' and type(key) is str:
            store.write_cell('global:'+obj.metadata['module']+':'+key,value,weak=weak,ops=self.ops)
        elif mode=='class_locals' and type(key) is str:
            store.write_cell(obj.metadata['prefix']+key,value,weak=weak,ops=self.ops)
        elif mode=='object':store.write_slot(obj.metadata['owner'],key,value,self.ops,weak=weak)
        else:store.write_slot(identifier,key,value,self.ops,weak=weak)
    def key_cases(self,key,store,frame,node):
        """Hashable scalar alternatives and effectful custom-key complements.

A None key is a genuine scalar key. ``None`` in the returned key-list position
means a non-finite set of hashable keys, never the target None constant.
"""
        self._current(store)
        key=self.ops.normalize(key)
        scalar=replace(key,refs=frozenset());cases=[];errors=[]
        unhashable=scalar.top_sorts & {'list','dict','set','bytearray'}
        if unhashable:errors.append(self.t.error(store.copy(),'TypeError'))
        safe=scalar.top_sorts-{'list','dict','set','bytearray'}
        concrete=replace(scalar,top_sorts=frozenset(),top=False,unbound=False)
        if not concrete.is_bottom():cases.append((store.copy(),concrete.exact_constants()))
        if safe or scalar.top:
            builtin_sorts={'str','int','bool','float','none','bytes'}
            if safe and safe<=builtin_sorts and not scalar.top:cases.append((store.copy(),None))
            else:
                for out in self.t.unknown(store,node,'unknown_dictionary_key_protocol'):
                    if out.kind=='normal':cases.append((out.store,None))
                    else:errors.append(out)
        for ref in key.refs:
            obj=store.heap.get(ref)
            if obj and obj.kind in {'list','dict','set'}:errors.append(self.t.error(store.copy(),'TypeError'));continue
            hashes=self.ops.join_all(self.t.lookup_class(cls,'__hash__',store) for cls in obj.class_ids) if obj else Value.bottom()
            if hashes.none:errors.append(self.t.error(store.copy(),'TypeError'))
            callable_hash=replace(hashes,none=False)
            if not callable_hash.is_bottom():
                for out in self.t.attributes._invoke_bound(callable_hash,Value.ref(ref),[],(),store.copy(),frame,node):
                    if out.kind!='normal':errors.append(out);continue
                    # Equality and hash collisions can invoke further source code.
                    # Until that relation is represented, use actual open effects.
                    has_integer=not out.value.ints.is_bottom or bool(out.value.bools)
                    if has_integer or out.value.top or out.value.top_sorts:
                        for unknown in self.t.unknown(out.store,node,'custom_dictionary_key_equality'):
                            if unknown.kind=='normal':cases.append((unknown.store,None))
                            else:errors.append(unknown)
                    if not has_integer or out.value.top:errors.append(self.t.error(out.store.copy(),'TypeError'))
            elif not hashes.none:
                for out in self.t.unknown(store,node,'unmodeled_dictionary_key_hash'):
                    if out.kind=='normal':cases.append((out.store,None))
                    else:errors.append(out)
        return cases,errors
    def key_alternatives(self,key,store,frame,node):
        """Compatibility hook for scalar-only consumers; protocols stay separate."""
        cases,errors=self.key_cases(key,store,frame,node)
        scalar=replace(key,refs=frozenset(),top=False,top_sorts=key.top_sorts&{'str','int','bool','float','none','bytes'})
        for state,constants in cases:
            if constants is None and scalar.is_bottom():
                errors.append(Flow(state,value=Value.unknown()))
        return scalar,errors
    def getitem(self,identifier,key,store,frame,node):
        self._current(store)
        cases,result=self.key_cases(key,store,frame,node)
        for state,constants in cases:
            self._current(state);identifier=self.ops.arena.resolve(identifier)
            obj=state.heap.get(identifier)
            if obj is None or obj.kind!='namespace':
                result.extend(self.t.unknown(state,node,'reabstracted_namespace_identity'));continue
            if self._havoc(obj,state):
                result.extend(self.t.unknown(state,node,'havoc_namespace_read'));continue
            self.d.reads.add((frame.qualname,'<namespace:*>' if constants is None else repr(constants),self.d.point(node)))
            if constants is None:
                values=self.bindings(obj,state)
                value=self.ops.join_all((*values.values(),obj.default))
                if obj.havoc or (obj.metadata.get('namespace_kind')=='globals' and obj.metadata['module'] in state.havoc_namespaces):value=self.ops.join(value,Value.unknown())
                if not value.is_bottom():result.append(Flow(state.copy(),value=value))
                result.append(self.t.error(state.copy(),'KeyError'))
            else:
                for concrete in constants:
                    value,missing=self._read(obj,concrete,state)
                    if not value.is_bottom():result.append(Flow(state.copy(),value=value))
                    if missing:result.append(self.t.error(state.copy(),'KeyError'))
        return result
    def setitem(self,identifier,key,value,store,frame,node):
        self._current(store)
        obj=store.heap[identifier]
        if self._havoc(obj,store):return self.t.unknown(store,node,'havoc_namespace_write')
        if obj.metadata.get('readonly'):return [self.t.error(store,'TypeError')]
        cases,result=self.key_cases(key,store,frame,node)
        for state,constants in cases:
            self._current(state);identifier=self.ops.arena.resolve(identifier)
            if identifier not in state.heap:
                result.extend(self.t.unknown(state,node,'reabstracted_namespace_write'));continue
            state.invalidate_guards()
            if constants is None:
                for known in self.bindings(state.heap[identifier],state):self._write(identifier,known,value,state,weak=True)
                target=state.heap[identifier];target.default=self.ops.join(target.default,value);target.havoc=True
                state.writes.add(('heap_default',identifier,None))
                if target.metadata.get('namespace_kind')=='globals':
                    module=target.metadata['module'];state.havoc_namespaces|=frozenset((module,))
            else:
                for concrete in constants:
                    branch=state.copy();self._write(identifier,concrete,value,branch)
                    self.d.writes.add((frame.qualname,repr(concrete),self.d.point(node)))
                    target=branch.heap[identifier];mode=target.metadata.get('namespace_kind')
                    if self.d.oracle is not None:
                        if mode=='globals' and type(concrete) is str and hasattr(self.d.oracle,'publish_cell'):
                            cell='global:'+target.metadata['module']+':'+concrete
                            self.d.oracle.publish_cell(cell,branch.effective_cell(cell),'namespace_write')
                        elif hasattr(self.d.oracle,'publish_field'):
                            owner=target.metadata.get('owner',identifier)
                            self.d.oracle.publish_field(owner,concrete,value,'namespace_write')
                    result.append(self.t.normal(branch))
                continue
            self.d.writes.add((frame.qualname,'<namespace:*>',self.d.point(node)))
            result.append(self.t.normal(state))
        return result
    def contains(self,identifier,key,store,frame,node):
        result=[]
        for flow in self.getitem(identifier,key,store,frame,node):
            if flow.kind=='normal':result.append(Flow(flow.store,value=Value.constant(True)))
            elif flow.exception=='KeyError':result.append(Flow(flow.store,value=Value.constant(False)))
            else:result.append(flow)
        return result
    def builtin(self,name,args,keywords,store,frame,node):
        if keywords:return [self.t.error(store,'TypeError')]
        if name in {'globals','locals'}:
            if args:return [self.t.error(store,'TypeError')]
            return [Flow(store,value=self.global_view(frame.module,store) if name=='globals' else self.local_view(frame,store))]
        if name=='vars':
            if not args:return [Flow(store,value=self.local_view(frame,store))]
            if len(args)!=1:return [self.t.error(store,'TypeError')]
            return self.t.getattr(args[0],'__dict__',store,frame,node)
        raise ValueError('not a namespace builtin: '+name)
    def method(self,name,receiver,args,keywords,store,frame,node):
        self._current(store)
        if hasattr(self.ops,'normalize'):receiver=self.ops.normalize(receiver)
        result=[]
        for ref in receiver.refs:
            obj=store.heap.get(ref)
            if not obj or obj.kind!='namespace':continue
            if self._havoc(obj,store):result.extend(self.t.unknown(store,node,'havoc_namespace_state'));continue
            if name=='get' and args:
                default=args[1] if len(args)>1 else Value.constant(None)
                for out in self.getitem(ref,args[0],store.copy(),frame,node):result.append(Flow(out.store,value=default) if out.exception=='KeyError' else out)
            elif name in {'keys','values','items','copy'} and not args:
                state=store.copy();bindings=self.bindings(state.heap[ref],state)
                if name=='copy':
                    value=self.t.alloc(node,state,frame,'dict',dict(bindings),{'length':len(bindings)})
                else:
                    values=[]
                    for key,value in bindings.items():
                        if name=='keys':values.append(Value.constant(key))
                        elif name=='values':values.append(value)
                        else:values.append(self.t.alloc(node,state,frame,'tuple',{0:Value.constant(key),1:value},{'length':2}))
                    value=self.t.alloc(node,state,frame,'list',{i:value for i,value in enumerate(values)},{'length':len(values)})
                result.append(Flow(state,value=value))
            elif name=='setdefault' and args:
                default=args[1] if len(args)>1 else Value.constant(None)
                for out in self.getitem(ref,args[0],store.copy(),frame,node):
                    if out.exception=='KeyError':
                        result.extend(Flow(saved.store,saved.kind,default if saved.kind=='normal' else saved.value,saved.exception) for saved in self.setitem(ref,args[0],default,out.store,frame,node))
                    else:result.append(out)
            else:result.extend(self.t.unknown(store,node,'unsupported_namespace_method:'+name))
        return result
