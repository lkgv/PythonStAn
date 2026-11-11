"""Fair or legacy arbitration over Native's four persistent work queues."""


class NativeScheduler:
    KIND_ORDER=("static","constraint_replay","builtin_replay","points_to")

    def __init__(self,policy):
        if policy not in {"fair","legacy"}:
            raise ValueError("Unknown native scheduling policy")
        self.policy=policy

    def select(self,queues,cursor,*,policy=None):
        if len(queues)!=len(self.KIND_ORDER):
            raise ValueError("Native scheduler requires all four work kinds")
        fair=(self.policy if policy is None else policy)=="fair"
        start=cursor if fair else 0
        for offset in range(len(self.KIND_ORDER)):
            index=(start+offset)%len(self.KIND_ORDER)
            if len(queues[index]):
                return self.KIND_ORDER[index],((index+1)%len(self.KIND_ORDER) if fair else cursor)
        return None,cursor

    def pop(self,queue,*,policy=None):
        return queue.popleft() if (self.policy if policy is None else policy)=="fair" else queue.pop()

    def pending(self,queues):
        if len(queues)!=len(self.KIND_ORDER):
            raise ValueError("Native scheduler requires all four work kinds")
        return {kind:len(queue) for kind,queue in zip(self.KIND_ORDER,queues)}
