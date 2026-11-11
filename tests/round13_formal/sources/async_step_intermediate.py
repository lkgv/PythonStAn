events = []


def suspension_callback():
    events.append("suspension")


def item_callback():
    events.append("item")


class Pause:
    def __await__(self):
        yield suspension_callback


async def stream():
    await Pause()
    yield item_callback


generator = stream()
step = generator.__anext__()
token = step.send(None)
token()
