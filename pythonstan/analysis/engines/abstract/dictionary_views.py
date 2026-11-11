"""Live builtin dictionary views and their finite ordered iterators.

A view stores its owner, never a snapshot of its elements. Iterator keys are
captured only when iteration starts, so size changes raise at the next step.
"""
from __future__ import annotations
import ast
from dataclasses import replace
from pythonstan.analysis.domains.values import Value
from .state import Flow


class DictionaryViews:
    def __init__(self,transfer):self.t=transfer;self.d=transfer.d;self.ops=transfer.ops

    def create(self,owner,kind,store,frame,node):
        return self.t.alloc(node,store,frame,'dict_view',metadata={'owner':Value.ref(owner),'view_kind':kind})

    def length(self,view,store,frame,node):
        return self.t.length_builtin([view.metadata['owner']],(),store,frame,node)

    def iterator(self,owner,kind,store,frame,node):
        result=[]
        for ref in owner.refs:
            current=store.copy();obj=current.heap.get(ref)
            if obj is None or obj.kind!='dict' or obj.havoc or not obj.singleton or obj.maybe_missing or not obj.default.is_bottom():
                result.extend(self.t.unknown(current,node,'abstract_dictionary_view_iteration'));continue
            metadata={'container':Value.ref(ref),'dictionary_iteration':kind,'dictionary_keys':tuple(obj.fields),'index':0}
            result.append(Flow(current,value=self.t.alloc(node,current,frame,'iterator',metadata=metadata)))
        if owner.top or owner.top_sorts:result.extend(self.t.unknown(store,node,'unknown_dictionary_view_owner'))
        return result

    def next(self,iterator,store,frame,node):
        item=store.heap[iterator];index=item.metadata['index'];kind=item.metadata['dictionary_iteration'];result=[]
        if item.metadata.get('dictionary_exhausted'):return [self.t.continuations.stop(store,frame,node)]
        for owner in item.metadata['container'].refs:
            current=store.copy();source=current.heap.get(owner)
            if source is None or source.havoc or not source.singleton or source.maybe_missing or not source.default.is_bottom():
                result.extend(self.t.unknown(current,node,'abstract_dictionary_iterator_state'));continue
            keys=tuple(source.fields);initial=item.metadata['dictionary_keys']
            if len(keys)!=len(initial):result.append(self.t.error(current,'RuntimeError'));continue
            if keys!=initial:
                result.extend(self.t.unknown(current,node,'same_size_dictionary_key_replacement'));continue
            if index>=len(keys):
                current.heap[iterator].metadata['dictionary_exhausted']=True
                current.writes.add(('heap_meta',iterator,'iterator_state'))
                result.append(self.t.continuations.stop(current,frame,node));continue
            key=keys[index]
            if type(key) not in {str,int,bool,float,type(None)}:
                result.extend(self.t.unknown(current,node,'dictionary_view_key_representation'));continue
            answer=Value.constant(key) if kind=='keys' else source.fields[key]
            if kind=='items':
                synthetic=ast.copy_location(ast.Constant(value=None),node)
                self.d.register_generated(synthetic,self.d.point(node)+'#dictionary-item:'+iterator+':'+str(index))
                answer=self.t.alloc(synthetic,current,frame,'tuple',{0:Value.constant(key),1:answer},{'length':2})
            current.heap[iterator].metadata['index']=index+1
            current.writes.add(('heap_meta',iterator,'iterator_state'))
            result.append(Flow(current,value=answer))
        return result

    def contains(self,view,key,store,frame,node):
        if view.havoc:return self.t.unknown(store,node,'havoc_dictionary_view_membership')
        owner=view.metadata['owner'];kind=view.metadata['view_kind']
        if kind=='keys':return self.t.dictionaries.contains(owner,key,store,frame,node)
        result=[]
        for ref in owner.refs:
            source=store.heap.get(ref)
            if source is None or source.havoc or source.maybe_missing or not source.default.is_bottom():
                result.extend(self.t.unknown(store,node,'abstract_dictionary_view_membership'));continue
            needles=[]
            if kind=='values':needles=[(None,key)]
            else:
                for candidate in key.refs:
                    pair=store.heap.get(candidate)
                    if pair and pair.kind=='tuple' and not pair.havoc and pair.metadata.get('length')==2:
                        needles.append((pair.fields.get(0,pair.default),pair.fields.get(1,pair.default)))
                    elif pair and pair.kind=='tuple' and type(pair.metadata.get('length')) is int:
                        result.append(Flow(store.copy(),value=Value.constant(False)))
                    else:result.extend(self.t.unknown(store,node,'dictionary_items_equality_protocol'))
                scalar=replace(key,refs=frozenset())
                if scalar.top or scalar.top_sorts:result.extend(self.t.unknown(store,node,'dictionary_items_equality_protocol'))
                elif not scalar.is_bottom():result.append(Flow(store.copy(),value=Value.constant(False)))
            for wanted_key,wanted_value in needles:
                constants=wanted_value.exact_constants();keys=wanted_key.exact_constants() if wanted_key is not None else [None]
                values=[value.exact_constants() for value in source.fields.values()]
                if constants is None or keys is None or any(value is None for value in values):
                    result.extend(self.t.unknown(store,node,'dictionary_view_equality_protocol'));continue
                truths=set()
                for target_key in keys:
                    for target_value in constants:
                        comparisons=[(wanted_key is None or concrete_key==target_key,[v==target_value for v in choices]) for concrete_key,choices in zip(source.fields,values)]
                        if any(matches_key and any(matches) for matches_key,matches in comparisons):truths.add(True)
                        if all(not matches_key or not matches or not all(matches) for matches_key,matches in comparisons):truths.add(False)
                if truths:result.append(Flow(store.copy(),value=Value(bools=frozenset(truths))))
        return result
