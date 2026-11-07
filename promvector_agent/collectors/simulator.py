import random

from ..models import Metric, Sample
from . import Collection


class Simulator:
    def __init__(self, randomizer: random.Random | None = None):
        self.random = randomizer or random.Random()

    async def test(self) -> None:
        pass

    async def collect(self):
        running = self.random.random() > 0.06
        yield Collection(
            [
                Sample(
                    metrics={
                        "temperature": Metric(value=round(self.random.uniform(65, 87), 2), unit="C"),
                        "pressure": Metric(value=round(self.random.uniform(5.5, 7.5), 2), unit="bar"),
                        "rpm": Metric(value=self.random.randint(1420, 1490) if running else 0, unit="rpm"),
                        "vibration": Metric(value=round(self.random.uniform(0.5, 3.2), 2), unit="mm/s"),
                        "operating_state": Metric(value="running" if running else "stopped"),
                    }
                )
            ]
        )
