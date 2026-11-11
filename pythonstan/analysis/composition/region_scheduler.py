"""Execute a frozen source-bound region plan through explicit adapters."""
from __future__ import annotations
from dataclasses import replace
from pythonstan.analysis.domains.values import ValueOperations,ValuePolicy
from .state_adapter import reabstract_state
from .region_engine import NativeRegionEngine,validate_region_plan

class RegionScheduler:
    def __init__(self,plan):self.plan=plan;self.native=NativeRegionEngine();self.transitions=[]
    def validate(self,driver):return validate_region_plan(driver,self.plan)
    def execute(self,node,flow,frame,driver):
        decision=self.plan.decision(frame.module,frame.qualname,driver.point(node))
        overrides=decision.get("abstract",{});old_ops=driver.ops;old_abstract=driver.abstract;old_transfer_ops=driver.transfer.ops
        adjusted=flow
        if overrides:
            settings={**driver.abstract,**overrides}
            policy=replace(old_ops.policy,max_const_strings=settings.get("max_const_strings",old_ops.policy.max_const_strings),max_const_ints=settings.get("int_domain_max_consts",old_ops.policy.max_const_ints),max_addr_set_size=settings.get("max_addr_set_size",old_ops.policy.max_addr_set_size),max_scalar_pairs=settings.get("max_scalar_pairs",old_ops.policy.max_scalar_pairs))
            new_ops=ValueOperations(policy,arena=old_ops.arena);adjusted=reabstract_state(flow,new_ops)
            driver.ops=new_ops;driver.transfer.ops=new_ops;driver.abstract=settings
            driver.stats["domain_region_adaptations"]+=1
            self.transitions.append({"kind":"scalar_domain","point":driver.point(node),"before":old_ops.policy.__dict__,"after":policy.__dict__})
        try:
            if decision["engine"]=="native":
                outputs=self.native.execute(node,adjusted,frame,driver)
                self.transitions.append({"kind":"engine","point":driver.point(node),"from":"ai_primary","to":"native","return_to":"ai_primary","region_id":decision.get("region_id")})
            else:outputs=driver.transfer.statement(node,adjusted.store,frame)
            outputs=driver.coarsen_flows(outputs,driver.ops)
        finally:driver.ops=old_ops;driver.transfer.ops=old_transfer_ops;driver.abstract=old_abstract
        return outputs
