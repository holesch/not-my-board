#!/usr/bin/python3

class Preset:
    @classmethod
    def from_name(cls, name):
        return cls()

    async def filter(self, places):
        # TODO filter
        return [place["id"] for place in places]
