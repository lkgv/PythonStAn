async def stream():
    yield


async def consume():
    item = await anext(stream())
    return item


pending = consume()
try:
    pending.send(None)
except StopIteration:
    pass
