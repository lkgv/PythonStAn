events = []


def suspension_callback():
    events.append("suspension")


def completed_callback():
    events.append("completed")


class Pause:
    def __await__(self):
        yield suspension_callback


async def consume():
    await Pause()
    return completed_callback


pending = consume()
iterator = pending.__await__()
token = iterator.send(None)
token()
