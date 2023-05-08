from typing import List


class Namespace:
    names: List[str]

    def __init__(self, names: List[str]):
        self.names = names

    def __str__(self):
        return '.'.join(self.names)
