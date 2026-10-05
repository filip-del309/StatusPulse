from abc import ABC, abstractmethod


class Source(ABC):

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    async def run(self) -> None:
        ...
