events = []


def yielded_callback():
    events.append("yielded")


def completed_callback():
    events.append("completed")


class Awaitable:
    def __await__(self):
        yield yielded_callback
        return completed_callback


async def consume():
    result = await Awaitable()
    result()


pending = consume()
pending.send(None)
try:
    pending.send(None)
except StopIteration:
    pass
